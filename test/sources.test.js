// 各国データ取得処理のパーサーを、公表されている形式どおりの小さなサンプルで確認する
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deflateRawSync } from 'node:zlib';
import { Accumulator } from '../src/aggregate.js';
import { csvRecords, parseCsvLine } from '../scripts/lib/csv.js';
import { listZip, readZipEntry } from '../scripts/lib/zip.js';
import * as jp from '../scripts/sources/jp.js';
import * as gb from '../scripts/sources/gb.js';
import * as fr from '../scripts/sources/fr.js';
import * as sg from '../scripts/sources/sg.js';
import * as tw from '../scripts/sources/tw.js';
import * as kr from '../scripts/sources/kr.js';
import { parseBis, parseFhfa, parseOecd } from '../scripts/sources/indicators.js';

// テスト用に deflate 圧縮の ZIP を組み立てる
export function makeZip(files) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const nameBuf = Buffer.from(name);
    const data = deflateRawSync(Buffer.from(content));
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(8, 8);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt16LE(nameBuf.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(8, 10);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt16LE(nameBuf.length, 28);
    central.writeUInt32LE(offset, 42);
    locals.push(local, nameBuf, data);
    centrals.push(central, nameBuf);
    offset += local.length + nameBuf.length + data.length;
  }
  const cd = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(files).length, 8);
  end.writeUInt16LE(Object.keys(files).length, 10);
  end.writeUInt32LE(cd.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, end]);
}

const only = (acc) => [...acc.datasets()];

test('CSV: 引用符・カンマ・エスケープ', () => {
  assert.deepEqual(parseCsvLine('a,"b,c","d ""e""",'), ['a', 'b,c', 'd "e"', '']);
  assert.deepEqual([...csvRecords(['﻿x,y', '1,2'])], [{ x: '1', y: '2' }]);
});

test('ZIP: deflate エントリを取り出せる', () => {
  const zip = makeZip({ 'a.txt': 'hello', 'dir/b.csv': 'x,y\n1,2' });
  const entries = listZip(zip);
  assert.deepEqual(entries.map((e) => e.name), ['a.txt', 'dir/b.csv']);
  assert.equal(readZipEntry(zip, entries[1]).toString(), 'x,y\n1,2');
});

test('JP: 期間の解釈と種別の絞り込み', () => {
  assert.deepEqual(jp.parsePeriod('２０２４年第３四半期'), { year: 2024, quarter: 3 });
  const t = jp.toTransaction('13', '東京都', { Type: '中古マンション等', MunicipalityCode: '13104', Municipality: '新宿区', DistrictName: '西新宿', TradePrice: '50000000', Area: '50', Period: '2024年第1四半期' });
  assert.equal(t.quarter, 1);
  assert.equal(jp.toTransaction('13', '東京都', { Type: '不明', Period: '2024年第1四半期' }), null);
});

test('GB: Price Paid Data の行を解釈し、カテゴリ B は除く', () => {
  const line = (price, type, cat) =>
    `"{A1}","${price}","2024-05-17 00:00","SW1A 1AA","${type}","N","L","10","FLAT 2","DOWNING ST","","LONDON","CITY OF WESTMINSTER","GREATER LONDON","${cat}","A"`;
  const t = gb.toTransaction(line(750000, 'F', 'A'));
  assert.deepEqual(
    { region: t.region, regionName: t.regionName, city: t.city, cityName: t.cityName, district: t.district, type: t.type, year: t.year, quarter: t.quarter },
    { region: 'greater-london', regionName: 'Greater London', city: 'city-of-westminster', cityName: 'City Of Westminster', district: 'SW1A', type: 'flat', year: 2024, quarter: 2 },
  );
  assert.equal(gb.toTransaction(line(750000, 'F', 'B')), null);
  const acc = new Accumulator({ country: 'GB', currency: 'GBP' });
  acc.add(t);
  const [ds] = only(acc);
  // 面積がないので㎡単価は null
  assert.deepEqual(ds.rows.find((r) => r[1] === 'SW1A' && r[3] === '2024').slice(4), [1, 750000, null, null, null]);
});

test('FR: 同じ取引の行をまとめ、住宅1戸の売買だけを対象にする', async () => {
  const header = 'id_mutation,date_mutation,nature_mutation,valeur_fonciere,code_commune,nom_commune,code_departement,type_local,surface_reelle_bati';
  const rows = [
    '2024-1,2024-03-02,Vente,400000,75111,Paris 11e Arrondissement,75,Appartement,40',
    '2024-1,2024-03-02,Vente,400000,75111,Paris 11e Arrondissement,75,Dépendance,', // 付属の駐車場は許容
    '2024-2,2024-04-10,Vente,900000,75111,Paris 11e Arrondissement,75,Appartement,50', // 2戸の一括売買は除外
    '2024-2,2024-04-10,Vente,900000,75111,Paris 11e Arrondissement,75,Appartement,45',
    '2024-3,2024-05-01,Echange,300000,75111,Paris 11e Arrondissement,75,Appartement,30', // 交換は除外
    '2024-4,2024-11-20,Vente,1200000,75116,Paris 16e Arrondissement,75,Maison,120',
  ];
  async function* gen() {
    yield* csvRecords([header, ...rows]);
  }
  const txs = [];
  for await (const g of fr.groupMutations(gen())) {
    const t = fr.toTransaction(g);
    if (t) txs.push(t);
  }
  assert.deepEqual(txs.map((t) => [t.city, t.type, t.quarter, Number(t.price) / Number(t.area)]), [
    ['75111', 'Appartement', 1, 10000],
    ['75116', 'Maison', 4, 10000],
  ]);
  assert.equal(txs[0].regionName, 'Paris');
});

test('SG: HDB 再販価格', () => {
  const [rec] = csvRecords([
    'month,town,flat_type,block,street_name,storey_range,floor_area_sqm,flat_model,lease_commence_date,remaining_lease,resale_price',
    '2024-08,ANG MO KIO,4 ROOM,406,ANG MO KIO AVE 10,07 TO 09,92,New Generation,1979,54 years 01 month,600000',
  ]);
  const t = sg.toTransaction(rec);
  assert.deepEqual([t.region, t.city, t.cityName, t.type, t.year, t.quarter], ['sg', 'ang-mo-kio', 'Ang Mo Kio', '4 ROOM', 2024, 3]);
  const acc = new Accumulator({ country: 'SG', currency: 'SGD' });
  acc.add(t);
  assert.equal(only(acc)[0].rows[0][6], Math.round(600000 / 92));
});

test('TW: 實價登錄の ZIP（県市別 CSV・2行目は英語見出し）', () => {
  assert.deepEqual(tw.parseRocDate('1130105'), { year: 2024, quarter: 1 });
  assert.equal(tw.parseRocDate('1131305'), null);
  const csv = [
    '鄉鎮市區,交易標的,交易年月日,建物型態,建物移轉總面積平方公尺,總價元,單價元平方公尺,備註',
    'The villages and towns urban district,transaction sign,transaction year month and day,building state,building shifting total area,total price NTD,the unit price (NTD / square meter),the note',
    '大安區,房地(土地+建物),1130105,住宅大樓(11層含以上有電梯),100,30000000,300000,',
    '大安區,房地(土地+建物),1130220,住宅大樓(11層含以上有電梯),80,20000000,250000,親友、員工或其他特殊關係間之交易', // 除外
    '大安區,土地,1130220,其他,0,5000000,,', // 土地のみは除外
  ].join('\n');
  const zip = makeZip({ 'a_lvr_land_a.csv': csv, 'a_lvr_land_c.csv': 'rent data', 'manifest.csv': 'x' });
  const acc = new Accumulator({ country: 'TW', currency: 'TWD' });
  assert.equal(tw.addSeason(acc, zip), 1);
  const [ds] = only(acc);
  assert.deepEqual([ds.region, ds.types, ds.rows[0].slice(4, 7)], [{ code: 'a', name: '臺北市' }, ['住宅大樓'], [1, 30000000, 300000]]);
});

test('KR: 実取引価格 API の XML（万ウォン・解除取引の除外）', () => {
  const xml = `<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header>
  <body><items>
    <item><aptNm>래미안</aptNm><dealAmount>   185,000</dealAmount><dealYear>2024</dealYear><dealMonth>7</dealMonth><dealDay>3</dealDay><excluUseAr>84.97</excluUseAr><sggCd>11680</sggCd><umdNm>대치동</umdNm><cdealType> </cdealType></item>
    <item><aptNm>취소건</aptNm><dealAmount>100,000</dealAmount><dealYear>2024</dealYear><dealMonth>7</dealMonth><excluUseAr>59</excluUseAr><umdNm>대치동</umdNm><cdealType>O</cdealType></item>
  </items><numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>2</totalCount></body></response>`;
  const { items, totalCount } = kr.parseItems(xml);
  assert.equal(totalCount, 2);
  const txs = items.map((i) => kr.toTransaction('11680', i)).filter(Boolean);
  assert.equal(txs.length, 1);
  assert.deepEqual([txs[0].region, txs[0].regionName, txs[0].cityName, txs[0].district, txs[0].price, txs[0].quarter], ['11', '서울특별시', '강남구', '대치동', 1850000000, 3]);
  assert.throws(() => kr.parseItems('<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>'), /認証エラー/);
});

test('指標: BIS / OECD / FHFA', () => {
  const bis = parseBis(
    [
      'FREQ:Frequency,REF_AREA:Reference area,VALUE:Value,UNIT_MEASURE:Unit of measure,TIME_PERIOD:Time period,OBS_VALUE:Observation Value',
      'Q,JP,N,628,2023-Q4,120.5',
      'Q,JP,N,628,2024-Q4,126.5',
      'Q,JP,R,628,2024-Q4,110',
      'Q,US,N,628,2024-Q4,210',
      'Q,US,N,628,2025-Q1,',
    ].join('\n'),
  );
  assert.deepEqual(bis.series.map((s) => [s.country, s.indicator, s.points.length]), [['JP', 'bis_nominal', 2], ['JP', 'bis_real', 1], ['US', 'bis_nominal', 1]]);

  const oecdRow = (area, name, freq, adj, period, value) =>
    `DATAFLOW,X,I,${area},${name},${freq},x,RPI,Rent prices,IX,"Index, 2015=100",${adj},x,${period},${period},${value},${value}`;
  const oecd = parseOecd(
    [
      'STRUCTURE,STRUCTURE_ID,ACTION,REF_AREA,Reference area,FREQ,Frequency of observation,MEASURE,Measure,UNIT_MEASURE,Unit of measure,ADJUSTMENT,Adjustment,TIME_PERIOD,Time period,OBS_VALUE,Observation value',
      oecdRow('JPN', 'Japan', 'Q', 'N', '2024-Q1', '105.2'),
      oecdRow('JPN', 'Japan', 'Q', 'S', '2024-Q1', '999'), // 季節調整済みは使わない
      oecdRow('OECD', 'OECD', 'Q', 'N', '2024-Q1', '110'),
      oecdRow('DEU', 'Germany', 'A', 'N', '2024', '120'), // 年次は使わない
    ].join('\n'),
  );
  assert.deepEqual(oecd.indicators.oecd_rpi_ix, { label: 'Rent prices', unit: 'Index, 2015=100', frequency: 'Q' });
  assert.deepEqual(oecd.series.map((s) => [s.country, s.points]), [['JP', [['2024Q1', 105.2]]], ['OECD', [['2024Q1', 110]]]]);

  const fhfa = parseFhfa(
    [
      'hpi_type,hpi_flavor,frequency,level,place_name,place_id,yr,period,index_nsa,index_sa',
      'traditional,purchase-only,quarterly,USA or Census Division,United States,USA,2024,1,420.1,421',
      'traditional,all-transactions,quarterly,State,California,CA,2024,1,1300.5,',
      'traditional,purchase-only,monthly,State,California,CA,2024,1,500,', // 月次は使わない
      'distress-free,purchase-only,quarterly,MSA,"San Francisco, CA",41884,2024,1,300,',
    ].join('\n'),
  );
  assert.deepEqual(
    fhfa.series.map((s) => [s.area, s.level, s.indicator, s.points[0]]),
    [['CA', 'State', 'fhfa_all_transactions', ['2024Q1', 1300.5]], ['', 'national', 'fhfa_purchase_only', ['2024Q1', 420.1]]],
  );
});
