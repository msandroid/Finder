// 韓国: 국토교통부 아파트 매매 실거래가 (공공데이터포털 API)。要 KOREA_DATA_API_KEY
// https://www.data.go.kr/data/15126469/openapi.do
import { Accumulator } from '../../src/aggregate.js';
import { COUNTRIES } from '../../src/countries.js';
import { writeDataset } from '../../src/write.js';
import { fetchRetry } from '../lib/net.js';

const ENDPOINT = 'https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade';

export const SIDO = { 11: '서울특별시', 26: '부산광역시', 28: '인천광역시' };

// 既定で取得する市郡区（법정동코드の上5桁）。API の1日あたり呼び出し上限を考慮して3都市に絞っている
export const SIGUNGU = {
  11110: '종로구', 11140: '중구', 11170: '용산구', 11200: '성동구', 11215: '광진구', 11230: '동대문구',
  11260: '중랑구', 11290: '성북구', 11305: '강북구', 11320: '도봉구', 11350: '노원구', 11380: '은평구',
  11410: '서대문구', 11440: '마포구', 11470: '양천구', 11500: '강서구', 11530: '구로구', 11545: '금천구',
  11560: '영등포구', 11590: '동작구', 11620: '관악구', 11650: '서초구', 11680: '강남구', 11710: '송파구',
  11740: '강동구',
  26110: '중구', 26140: '서구', 26170: '동구', 26200: '영도구', 26230: '부산진구', 26260: '동래구',
  26290: '남구', 26320: '북구', 26350: '해운대구', 26380: '사하구', 26410: '금정구', 26440: '강서구',
  26470: '연제구', 26500: '수영구', 26530: '사상구', 26710: '기장군',
  28110: '중구', 28140: '동구', 28177: '미추홀구', 28185: '연수구', 28200: '남동구', 28237: '부평구',
  28245: '계양구', 28260: '서구', 28710: '강화군', 28720: '옹진군',
};

const tag = (xml, name) => xml.match(new RegExp(`<${name}>\\s*([^<]*?)\\s*</${name}>`))?.[1] ?? '';

/** API レスポンス（XML）→ item ごとのオブジェクト */
export function parseItems(xml) {
  const code = tag(xml, 'resultCode');
  if (code && !/^0+$/.test(code)) throw new Error(`API エラー ${code}: ${tag(xml, 'resultMsg')}`);
  const auth = tag(xml, 'returnAuthMsg');
  if (auth) throw new Error(`認証エラー: ${auth}`);
  const items = [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)].map(([, body]) =>
    Object.fromEntries([...body.matchAll(/<(\w+)>\s*([^<]*?)\s*<\/\1>/g)].map(([, k, v]) => [k, v])),
  );
  return { items, totalCount: Number(tag(xml, 'totalCount')) || items.length };
}

/** item → Accumulator.add の引数。取引金額の単位は万ウォン。解除（취소）された取引は除く */
export function toTransaction(lawd, item) {
  if (item.cdealType === 'O') return null;
  const year = Number(item.dealYear);
  const month = Number(item.dealMonth);
  const man = Number(String(item.dealAmount ?? '').replace(/[,\s]/g, ''));
  if (!year || !(month >= 1 && month <= 12) || !man) return null;
  const sido = String(lawd).slice(0, 2);
  return {
    region: sido,
    regionName: SIDO[sido] ?? sido,
    city: String(lawd),
    cityName: SIGUNGU[lawd] ?? String(lawd),
    district: item.umdNm,
    type: '아파트',
    year,
    quarter: Math.ceil(month / 3),
    price: man * 10000,
    area: item.excluUseAr,
  };
}

export async function build({ out, from, to, only, env, log }) {
  let key = env.KOREA_DATA_API_KEY;
  if (!key) throw new Error('KOREA_DATA_API_KEY が未設定です（https://www.data.go.kr/ で「국토교통부_아파트 매매 실거래가 자료」を申請）');
  if (key.includes('%')) key = decodeURIComponent(key); // Encoding キーを渡された場合
  const codes = only ?? Object.keys(SIGUNGU);
  const now = new Date();
  const acc = new Accumulator({ country: 'KR', currency: COUNTRIES.KR.currency });
  const failures = [];
  let count = 0;

  for (const lawd of codes) {
    try {
      for (let year = from; year <= to; year++) {
        for (let month = 1; month <= 12; month++) {
          if (year === now.getFullYear() && month > now.getMonth() + 1) break;
          const ymd = `${year}${String(month).padStart(2, '0')}`;
          for (let page = 1; ; page++) {
            const qs = new URLSearchParams({ serviceKey: key, LAWD_CD: lawd, DEAL_YMD: ymd, pageNo: page, numOfRows: 1000 });
            const res = await fetchRetry(`${ENDPOINT}?${qs}`);
            if (!res) break;
            const { items, totalCount } = parseItems(await res.text());
            for (const item of items) if (acc.add(toTransaction(lawd, item) ?? {})) count++;
            if (page * 1000 >= totalCount) break;
          }
        }
      }
      log(`KR ${lawd} ${SIGUNGU[lawd] ?? ''}: 累計 ${count} 件`);
    } catch (err) {
      if (/認証|SERVICE_KEY/.test(err.message)) throw err;
      log(`KR ${lawd}: 失敗 — ${err.message}`);
      failures.push(lawd);
    }
  }
  if (count === 0) throw new Error('KR: レコードが0件でした');
  for (const ds of acc.datasets()) writeDataset(out, ds);
  return { failures };
}
