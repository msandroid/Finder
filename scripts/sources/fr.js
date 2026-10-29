// フランス: DVF（geo-dvf 版、県別・年別の CSV.gz）
// https://files.data.gouv.fr/geo-dvf/latest/csv/<年>/departements/<県>.csv.gz
import { Accumulator } from '../../src/aggregate.js';
import { COUNTRIES } from '../../src/countries.js';
import { writeDataset } from '../../src/write.js';
import { csvRecordsAsync } from '../lib/csv.js';
import { fetchRetry, lines } from '../lib/net.js';

const URL = (year, dep) => `https://files.data.gouv.fr/geo-dvf/latest/csv/${year}/departements/${dep}.csv.gz`;
const DWELLINGS = new Set(['Appartement', 'Maison']);

// DVF はアルザス・モゼル（57, 67, 68）とマヨットを含まない
export const DEPARTEMENTS = {
  '01': 'Ain', '02': 'Aisne', '03': 'Allier', '04': 'Alpes-de-Haute-Provence', '05': 'Hautes-Alpes',
  '06': 'Alpes-Maritimes', '07': 'Ardèche', '08': 'Ardennes', '09': 'Ariège', '10': 'Aube', '11': 'Aude',
  '12': 'Aveyron', '13': 'Bouches-du-Rhône', '14': 'Calvados', '15': 'Cantal', '16': 'Charente',
  '17': 'Charente-Maritime', '18': 'Cher', '19': 'Corrèze', '2A': 'Corse-du-Sud', '2B': 'Haute-Corse',
  '21': "Côte-d'Or", '22': "Côtes-d'Armor", '23': 'Creuse', '24': 'Dordogne', '25': 'Doubs', '26': 'Drôme',
  '27': 'Eure', '28': 'Eure-et-Loir', '29': 'Finistère', '30': 'Gard', '31': 'Haute-Garonne', '32': 'Gers',
  '33': 'Gironde', '34': 'Hérault', '35': 'Ille-et-Vilaine', '36': 'Indre', '37': 'Indre-et-Loire',
  '38': 'Isère', '39': 'Jura', '40': 'Landes', '41': 'Loir-et-Cher', '42': 'Loire', '43': 'Haute-Loire',
  '44': 'Loire-Atlantique', '45': 'Loiret', '46': 'Lot', '47': 'Lot-et-Garonne', '48': 'Lozère',
  '49': 'Maine-et-Loire', '50': 'Manche', '51': 'Marne', '52': 'Haute-Marne', '53': 'Mayenne',
  '54': 'Meurthe-et-Moselle', '55': 'Meuse', '56': 'Morbihan', '58': 'Nièvre', '59': 'Nord', '60': 'Oise',
  '61': 'Orne', '62': 'Pas-de-Calais', '63': 'Puy-de-Dôme', '64': 'Pyrénées-Atlantiques',
  '65': 'Hautes-Pyrénées', '66': 'Pyrénées-Orientales', '69': 'Rhône', '70': 'Haute-Saône',
  '71': 'Saône-et-Loire', '72': 'Sarthe', '73': 'Savoie', '74': 'Haute-Savoie', '75': 'Paris',
  '76': 'Seine-Maritime', '77': 'Seine-et-Marne', '78': 'Yvelines', '79': 'Deux-Sèvres', '80': 'Somme',
  '81': 'Tarn', '82': 'Tarn-et-Garonne', '83': 'Var', '84': 'Vaucluse', '85': 'Vendée', '86': 'Vienne',
  '87': 'Haute-Vienne', '88': 'Vosges', '89': 'Yonne', '90': 'Territoire de Belfort', '91': 'Essonne',
  '92': 'Hauts-de-Seine', '93': 'Seine-Saint-Denis', '94': 'Val-de-Marne', '95': "Val-d'Oise",
  '971': 'Guadeloupe', '972': 'Martinique', '973': 'Guyane', '974': 'La Réunion',
};

/**
 * 1件の取引（同じ id_mutation の行の集まり）→ Accumulator.add の引数。
 * 売買（Vente）で、住宅（Appartement / Maison）がちょうど1戸、それ以外の建物（店舗など）を含まないものだけを対象にする。
 * 付属物（Dépendance: 駐車場・物置）や土地だけの行は許容する。価格は取引全体の額なので、
 * 複数戸の一括売買を除かないと単価が歪む。
 */
export function toTransaction(rows) {
  const first = rows[0];
  if (!first || first.nature_mutation !== 'Vente') return null;
  const locals = rows.filter((r) => r.type_local && r.type_local !== 'Dépendance');
  if (locals.length !== 1 || !DWELLINGS.has(locals[0].type_local)) return null;
  const m = first.date_mutation.match(/^(\d{4})-(\d{2})/);
  if (!m) return null;
  const dep = first.code_departement;
  return {
    region: dep,
    regionName: DEPARTEMENTS[dep] ?? dep,
    city: first.code_commune,
    cityName: first.nom_commune,
    type: locals[0].type_local,
    year: Number(m[1]),
    quarter: Math.ceil(Number(m[2]) / 3),
    price: first.valeur_fonciere,
    area: locals[0].surface_reelle_bati,
  };
}

/** 行の列（id_mutation 順に並んでいる）を取引ごとにまとめる */
export async function* groupMutations(records) {
  let current = [];
  for await (const r of records) {
    if (current.length && r.id_mutation !== current[0].id_mutation) {
      yield current;
      current = [];
    }
    current.push(r);
  }
  if (current.length) yield current;
}

export async function build({ out, from, to, only, log }) {
  const deps = only ?? Object.keys(DEPARTEMENTS);
  const failures = [];
  for (const dep of deps) {
    try {
      const acc = new Accumulator({ country: 'FR', currency: COUNTRIES.FR.currency });
      let count = 0;
      for (let year = from; year <= to; year++) {
        const res = await fetchRetry(URL(year, dep));
        if (!res) continue; // DVF は直近約5年分のみ公開
        for await (const rows of groupMutations(csvRecordsAsync(lines(res, { gzip: true })))) {
          if (acc.add(toTransaction(rows) ?? {})) count++;
        }
      }
      if (count === 0) throw new Error('レコードが0件でした');
      for (const ds of acc.datasets()) writeDataset(out, ds);
      log(`FR ${dep} ${DEPARTEMENTS[dep] ?? ''}: ${count} 件`);
    } catch (err) {
      log(`FR ${dep}: 失敗 — ${err.message}`);
      failures.push(dep);
    }
  }
  return { failures };
}
