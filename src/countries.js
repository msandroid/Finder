// 取引データを収録する国の定義。地域（region）のファイルは data/<code 小文字>/<region>.json.gz
import { PREFECTURES } from './prefectures.js';

export const COUNTRIES = {
  JP: {
    name: '日本',
    currency: 'JPY',
    regionLabel: '都道府県',
    cityLabel: '市区町村',
    districtLabel: '地区',
    defaultType: '中古マンション等',
    unitPrice: '取引価格 ÷ 面積（公表の㎡単価があればその値）',
    source: '国土交通省 不動産情報ライブラリ（不動産取引価格情報）',
    // データ未生成でも都道府県名で引けるように静的に持つ
    regions: PREFECTURES.map(([code, name]) => ({ code, name, aliases: [name.replace(/[都道府県]$/, '')] })),
  },
  GB: {
    name: '英国（イングランド・ウェールズ）',
    currency: 'GBP',
    regionLabel: 'カウンティ',
    cityLabel: '地方自治体（district）',
    districtLabel: '郵便番号地区（outward code）',
    defaultType: 'flat',
    unitPrice: null,
    source: 'HM Land Registry Price Paid Data (Open Government Licence v3.0)',
  },
  FR: {
    name: 'フランス',
    currency: 'EUR',
    regionLabel: '県（département）',
    cityLabel: 'コミューン',
    districtLabel: null,
    defaultType: 'Appartement',
    unitPrice: '取引価格 ÷ 建物面積（surface réelle bâtie）',
    source: 'DVF – Demandes de valeurs foncières, DGFiP / Etalab (Licence Ouverte 2.0)',
  },
  SG: {
    name: 'シンガポール（HDB 公営住宅の再販）',
    currency: 'SGD',
    regionLabel: '国',
    cityLabel: 'タウン',
    districtLabel: null,
    defaultType: '4 ROOM',
    unitPrice: '再販価格 ÷ 床面積',
    source: 'Housing & Development Board, Resale flat prices (data.gov.sg, Singapore Open Data Licence)',
  },
  TW: {
    name: '台湾',
    currency: 'TWD',
    regionLabel: '直轄市・県市',
    cityLabel: '郷鎮市区',
    districtLabel: null,
    defaultType: '住宅大樓',
    unitPrice: '公表の単価（元/㎡）',
    source: '內政部 不動產交易實價查詢服務網（實價登錄, 政府資料開放授權條款）',
  },
  KR: {
    name: '韓国（マンション＝아파트）',
    currency: 'KRW',
    regionLabel: '広域自治体（시·도）',
    cityLabel: '市郡区（시군구）',
    districtLabel: '法定洞（법정동）',
    defaultType: '아파트',
    unitPrice: '取引価格 ÷ 専有面積',
    source: '국토교통부 아파트 매매 실거래가 (공공데이터포털)',
  },
};

export function resolveCountry(input) {
  const s = String(input ?? 'JP').trim().toUpperCase();
  const alias = { UK: 'GB', JPN: 'JP', GBR: 'GB', FRA: 'FR', SGP: 'SG', TWN: 'TW', KOR: 'KR', USA: 'US' }[s];
  return alias ?? s;
}
