const express = require("express");
const fetch   = require("node-fetch");
const fs      = require("fs");
const path    = require("path");
const cookieParser = require("cookie-parser");

const usersDb = require("./users");
const auth    = require("./auth");
const mailer  = require("./mailer");

const app = express();

// ── Stripe ───────────────────────────────────────────────────
const STRIPE_SECRET  = process.env.STRIPE_SECRET_KEY;
const STRIPE_WEBHOOK = process.env.STRIPE_WEBHOOK_SECRET;
const STRIPE_PRICE   = process.env.STRIPE_PRICE_ID;
const BASE_URL       = (process.env.BASE_URL || "https://90perc.hu").replace(/\/$/, "");
const stripe = STRIPE_SECRET ? require("stripe")(STRIPE_SECRET) : null;

// Stripe webhook – express.raw BEFORE global json middleware!
app.post("/api/stripe/webhook", express.raw({ type: "application/json" }), async (req, res) => {
  if (!stripe) return res.status(500).json({ error: "Stripe nincs konfigurálva" });
  let event;
  try {
    event = stripe.webhooks.constructEvent(req.body, req.headers["stripe-signature"], STRIPE_WEBHOOK);
  } catch (err) {
    console.error("Stripe webhook aláírás hiba:", err.message);
    return res.status(400).send(`Webhook Error: ${err.message}`);
  }

  // Azonnal 200-at küldünk – Stripe ne próbálja újraküldeni (duplikált számla ellen)
  res.json({ received: true });

  const updateUser = (customerId, email, patch) => {
    const u = (email && usersDb.findByEmail(email)) || usersDb.findByStripeCustomer(customerId);
    if (u) { usersDb.update(u.id, patch); return u; }
    return null;
  };

  if (event.type === "checkout.session.completed") {
    const s = event.data.object;
    let paidUntil = new Date(Date.now() + 31 * 24 * 60 * 60 * 1000).toISOString();
    // Stripe-tól lekérjük a tényleges előfizetési időszak végét
    if (s.subscription && stripe) {
      try {
        const sub = await stripe.subscriptions.retrieve(s.subscription);
        if (sub.current_period_end) {
          paidUntil = new Date(sub.current_period_end * 1000).toISOString();
        }
      } catch(e) { console.error("Stripe subscription lekérés hiba:", e.message); }
    }
    const u = updateUser(s.customer, s.customer_details?.email || s.customer_email, {
      plan: "pro", stripeCustomerId: s.customer, paidUntil,
      subscriptionStatus: "active", currentPeriodEnd: paidUntil,
      stripeSubscriptionId: s.subscription || null
    });
    if (u) {
      console.log(`Stripe ✓ előfizetés aktiválva: ${u.email}, lejár: ${paidUntil}`);
      mailer.sendPlanActivated(u.email, paidUntil).catch(e => console.error("Email hiba:", e.message));
    }
    else console.warn(`Stripe: felhasználó nem található – ${s.customer_details?.email}`);
  }

  if (event.type === "invoice.payment_succeeded") {
    const inv = event.data.object;
    if (inv.billing_reason === "subscription_cycle") {
      const periodEnd = inv.lines?.data?.[0]?.period?.end;
      const paidUntil = periodEnd
        ? new Date(periodEnd * 1000).toISOString()
        : new Date(Date.now() + 31 * 24 * 60 * 60 * 1000).toISOString();
      const u = updateUser(inv.customer, null, { paidUntil, subscriptionStatus: "active", currentPeriodEnd: paidUntil });
      if (u) console.log(`Stripe ✓ megújítva: ${u.email}, lejár: ${paidUntil}`);
    }
  }

  if (event.type === "customer.subscription.updated") {
    const sub  = event.data.object;
    const prev = event.data.previous_attributes || {};
    console.log(`Stripe subscription.updated – cancel_at_period_end: ${sub.cancel_at_period_end}, status: ${sub.status}, prev keys: [${Object.keys(prev).join(",")}], prev.cancel_at_period_end: ${prev.cancel_at_period_end}`);
    const justCancelled = sub.cancel_at_period_end &&
      sub.status === "active" &&
      ('cancel_at_period_end' in prev || prev.cancel_at_period_end === false);
    console.log(`  justCancelled: ${justCancelled}`);
    if (justCancelled) {
      const cancelledAt  = new Date().toISOString();
      const periodEndTs  = sub.cancel_at || sub.current_period_end;
      const periodEnd    = periodEndTs ? new Date(periodEndTs * 1000).toISOString() : null;
      const patch        = { cancelledAt, ...(periodEnd ? { paidUntil: periodEnd } : {}) };
      const u = updateUser(sub.customer, null, patch);
      if (u) {
        const endStr = periodEnd ? new Date(periodEnd).toLocaleDateString("hu-HU") : (u.paidUntil ? new Date(u.paidUntil).toLocaleDateString("hu-HU") : "–");
        console.log(`Stripe: lemondva (időszak végéig aktív) – ${u.email}, lejár: ${endStr}`);
        const adminEmail = process.env.ADMIN_EMAIL;
        if (adminEmail) mailer.send({
          to: adminEmail,
          subject: `❌ Előfizetés lemondva – ${u.email}`,
          text: `${u.email} lemondta előfizetését. Aktív marad: ${endStr}`,
          html: `<p>A <b>${u.email}</b> lemondta a 90perc.hu előfizetését.</p><p>Aktív marad: <b>${endStr}</b></p>`,
        }).catch(e => console.error("Admin email hiba:", e.message));
        mailer.sendSubscriptionCancelled(u.email, periodEnd || u.paidUntil).catch(e => console.error("Email hiba:", e.message));
      }
    }
  }

  if (event.type === "customer.subscription.deleted") {
    const sub = event.data.object;
    const cancelledAt = new Date().toISOString();
    const u = updateUser(sub.customer, null, { plan: "free", paidUntil: null, subscriptionStatus: "cancelled", currentPeriodEnd: null, cancelledAt });
    if (u) {
      console.log(`Stripe: lemondva – ${u.email}`);
      mailer.sendSubscriptionExpired(u.email).catch(e => console.error("Email hiba:", e.message));
      // Admin értesítő
      const adminEmail = process.env.ADMIN_EMAIL;
      if (adminEmail) mailer.send({
        to: adminEmail,
        subject: `❌ Előfizetés lemondva – ${u.email}`,
        text: `${u.email} lemondta az előfizetését.`,
        html: `<p>A <b>${u.email}</b> felhasználó lemondta a 90perc.hu előfizetését.</p><p>Lemondás időpontja: ${cancelledAt}</p>`,
      }).catch(e => console.error("Admin email hiba:", e.message));
    }
  }

});

app.use(express.json());
app.use(cookieParser());
app.use(auth.attachUser);                    // minden kérésre beteszi a req.user-t
app.use(express.static(path.join(__dirname, "public")));
app.use('/api/odds', require('./routes/odds'));

const ADMIN_PWD     = process.env.ADMIN_PASSWORD;
const ODDS_API_KEY  = process.env.ODDS_API_KEY;
const TG_BOT_TOKEN  = process.env.TG_BOT_TOKEN;
const TG_CHAT_ID    = process.env.TG_CHAT_ID;
const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY;
const FOOTBALLDATA_TOKEN = process.env.FOOTBALLDATA_TOKEN;   // opcionális: 90 perces eredményhez (football-data.org)
const DATA_FILE     = "/data/history.json";
const SCHEDULE_FILE = "/data/lastRun.json";

// ── Perzisztens tárolás ───────────────────────────────────
function loadHistory() {
  try {
    if (!fs.existsSync("/data")) fs.mkdirSync("/data", { recursive: true });
    if (!fs.existsSync(DATA_FILE)) return [];
    return JSON.parse(fs.readFileSync(DATA_FILE, "utf8"));
  } catch (e) { console.error("History betöltési hiba:", e.message); return []; }
}

function saveHistory() {
  try { fs.writeFileSync(DATA_FILE, JSON.stringify(history, null, 2), "utf8"); }
  catch (e) { console.error("History mentési hiba:", e.message); }
}

function loadLastRun() {
  try {
    if (!fs.existsSync(SCHEDULE_FILE)) return null;
    return JSON.parse(fs.readFileSync(SCHEDULE_FILE, "utf8")).lastRun || null;
  } catch { return null; }
}

function saveLastRun() {
  try { fs.writeFileSync(SCHEDULE_FILE, JSON.stringify({ lastRun: new Date().toISOString() }), "utf8"); }
  catch (e) { console.error("LastRun mentési hiba:", e.message); }
}

// ── Sport térkép (csak foci) ──────────────────────────────
const SPORT_MAP = {
  // Nemzetközi / kupák
  "soccer_fifa_world_cup":               { sport: "soccer", label: "⚽ FIFA VB 2026" },
  "soccer_uefa_champs_league":           { sport: "soccer", label: "⚽ BL" },
  "soccer_uefa_europa_league":           { sport: "soccer", label: "⚽ EL" },
  "soccer_uefa_europa_conference_league":{ sport: "soccer", label: "⚽ Konferencia Liga" },
  "soccer_uefa_nations_league":          { sport: "soccer", label: "⚽ Nemzetek Ligája" },
  "soccer_conmebol_copa_libertadores":   { sport: "soccer", label: "⚽ Copa Libertadores" },
  "soccer_conmebol_copa_sudamericana":   { sport: "soccer", label: "⚽ Copa Sudamericana" },
  // Anglia
  "soccer_epl":                          { sport: "soccer", label: "⚽ Premier League" },
  "soccer_efl_champ":                    { sport: "soccer", label: "⚽ Championship" },
  "soccer_england_league1":              { sport: "soccer", label: "⚽ League One" },
  "soccer_england_league2":              { sport: "soccer", label: "⚽ League Two" },
  // Németország
  "soccer_germany_bundesliga":           { sport: "soccer", label: "⚽ Bundesliga" },
  "soccer_germany_bundesliga2":          { sport: "soccer", label: "⚽ 2. Bundesliga" },
  // Spanyolország
  "soccer_spain_la_liga":                { sport: "soccer", label: "⚽ La Liga" },
  "soccer_spain_segunda_division":       { sport: "soccer", label: "⚽ La Liga 2" },
  // Olaszország
  "soccer_italy_serie_a":                { sport: "soccer", label: "⚽ Serie A" },
  "soccer_italy_serie_b":                { sport: "soccer", label: "⚽ Serie B" },
  // Franciaország
  "soccer_france_ligue_one":             { sport: "soccer", label: "⚽ Ligue 1" },
  "soccer_france_ligue_two":             { sport: "soccer", label: "⚽ Ligue 2" },
  // Egyéb európai élvonalak
  "soccer_netherlands_eredivisie":       { sport: "soccer", label: "⚽ Eredivisie" },
  "soccer_portugal_primeira_liga":       { sport: "soccer", label: "⚽ Primeira Liga" },
  "soccer_belgium_first_div":            { sport: "soccer", label: "⚽ Belga élvonal" },
  "soccer_turkey_super_league":          { sport: "soccer", label: "⚽ Török Szuperliga" },
  "soccer_greece_super_league":          { sport: "soccer", label: "⚽ Görög Szuperliga" },
  "soccer_switzerland_superleague":      { sport: "soccer", label: "⚽ Svájci Superliga" },
  "soccer_austria_bundesliga":           { sport: "soccer", label: "⚽ Osztrák Bundesliga" },
  "soccer_denmark_superliga":            { sport: "soccer", label: "⚽ Dán Superliga" },
  "soccer_norway_eliteserien":           { sport: "soccer", label: "⚽ Norvég Eliteserien" },
  "soccer_sweden_allsvenskan":           { sport: "soccer", label: "⚽ Svéd Allsvenskan" },
  "soccer_poland_ekstraklasa":           { sport: "soccer", label: "⚽ Lengyel Ekstraklasa" },
  // Amerika / Ázsia / Óceánia
  "soccer_brazil_campeonato":            { sport: "soccer", label: "⚽ Brazil Serie A" },
  "soccer_argentina_primera_division":   { sport: "soccer", label: "⚽ Argentin Primera" },
  "soccer_usa_mls":                      { sport: "soccer", label: "⚽ MLS" },
  "soccer_mexico_ligamx":                { sport: "soccer", label: "⚽ Liga MX" },
  "soccer_japan_j_league":               { sport: "soccer", label: "⚽ J1 League" },
  "soccer_australia_aleague":            { sport: "soccer", label: "⚽ A-League" },
};

const EXCLUDED_BM = ["betfair_ex_eu", "betfair_ex_uk", "matchbook", "betfair_sb_uk", "smarkets"];
const WINDOW_HOURS = 36;   // meddig előre nézzen a tippekhez/kombikhoz (több meccs → kombi is összeáll)
const MIN_SINGLE_ODDS = 1.50;   // single tippnél minimum odds (a kombi lábakra NEM vonatkozik)

let history    = loadHistory();
console.log(`History betöltve: ${history.length} tipp`);

// Meglévő duplikált kombik eltávolítása (azonos láb-halmaz). Lezártat előnyben tartunk,
// egyébként a legrégebbit; a duplikátumokat töröljük.
(function dedupeExistingCombos() {
  const combos = history.filter(t => t.type === "combo").sort((a, b) => {
    const sa = (a.result && a.result !== "pending") ? 0 : 1;
    const sb = (b.result && b.result !== "pending") ? 0 : 1;
    return sa - sb || (a.addedAt || "").localeCompare(b.addedAt || "");
  });
  const seen = new Set(), remove = new Set();
  for (const c of combos) {
    const k = comboKey(c);
    if (seen.has(k)) remove.add(c.id); else seen.add(k);
  }
  if (remove.size) {
    history = history.filter(t => !remove.has(t.id));
    saveHistory();
    console.log(`Duplikált kombik eltávolítva: ${remove.size}`);
  }
})();

// Pending (még le nem zárt) AI tippek visszaállítása szerver-újraindítás után.
// FONTOS: dátumtól függetlenül minden pending tipp visszakerül, mert egy tipp
// gyakran az előző napon lett felvéve a mai/esti meccsre – ezeknek is látszaniuk kell.
let latestTips = [];   // (megszűnt value tippek – üresen tartva a kompatibilitásért)
let aiTips     = history.filter(t => t.type === "ai"    && (!t.result || t.result === "pending"));
let comboTips  = history.filter(t => t.type === "combo" && (!t.result || t.result === "pending"));
console.log(`Visszaállítva: ${aiTips.length} AI tipp + ${comboTips.length} kombi`);

// ── Magyar idő ────────────────────────────────────────────
function getHungarianTime() {
  const huStr = new Date().toLocaleString("en-US", { timeZone: "Europe/Budapest", hour12: false });
  const hu    = new Date(huStr);
  return { hour: hu.getHours(), minute: hu.getMinutes(), day: hu.getDay() };
}

function huTime(isoDate) {
  return new Date(isoDate).toLocaleString("hu-HU", {
    timeZone: "Europe/Budapest", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  });
}

function todayHU() {
  // "2026. 07. 02. 23:31:14" → "2026. 07. 02." (nap, nem csak év)
  const p = new Date().toLocaleString("hu-HU", { timeZone: "Europe/Budapest" }).split(" ");
  return p.slice(0, 3).join(" ");
}

// ── Telegram ──────────────────────────────────────────────
async function sendTelegram(text) {
  if (!TG_BOT_TOKEN || !TG_CHAT_ID) return;
  try {
    const r    = await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: TG_CHAT_ID, text, parse_mode: "HTML" })
    });
    const data = await r.json();
    if (!data.ok) console.error("Telegram hiba:", JSON.stringify(data));
    else console.log("Telegram: üzenet elküldve ✓");
  } catch (e) { console.error("Telegram hiba:", e.message); }
}

// ── Ázsiai kiértékelés ────────────────────────────────────
// Egész/fél vonal: won/lost/push. Negyedes vonal (x.25 / x.75): a tét két fél
// fogadásra bomlik a két szomszédos vonalon → won / half_won / push / half_lost / lost.
// x  = realizált érték (hendikepnél: sajátGól-ellenGól; over-nél: összGól)
// line = az a vonal, amit meg kell haladni a nyeréshez
function settleQuarter(x, line) {
  const L = Math.round(line * 4) / 4;            // 0.25-ös rácsra igazítás
  if (Number.isInteger(L * 2)) {                 // egész vagy fél vonal → egy fogadás
    return x > L ? "won" : x < L ? "lost" : "push";
  }
  const s1 = Math.sign(x - (L - 0.25));          // alsó fél-vonal
  const s2 = Math.sign(x - (L + 0.25));          // felső fél-vonal
  const sum = s1 + s2;                           // -2..+2
  if (sum === 2)  return "won";                  // mindkét fél nyer
  if (sum === 1)  return "half_won";             // egyik nyer, másik visszajár
  if (sum === -1) return "half_lost";            // egyik veszt, másik visszajár
  if (sum === 0)  return "push";                 // (negyedesnél gyakorlatilag nem fordul elő)
  return "lost";                                 // mindkét fél veszt
}

const SETTLED = ["won", "lost", "push", "half_won", "half_lost"];
// Egy lezárt tipp profitja egységben (1 = teljes tét).
function tipProfit(t) {
  const o = parseFloat(t.odds) || 0;
  switch (t.result) {
    case "won":       return o - 1;
    case "half_won":  return (o - 1) / 2;
    case "lost":      return -1;
    case "half_lost": return -0.5;
    default:          return 0;                  // push
  }
}
function nowHu() {
  return new Date().toLocaleString("hu-HU", { timeZone: "Europe/Budapest" });
}

// AI tippek: foci meccsek elemzése valós odds és web keresés alapján.
function isFootballAi(t) {
  if (t.type === "value") return false;
  return /soccer|foci|⚽/i.test((t.sport || "") + " " + (t.sportLabel || ""));
}

// ── Statisztika számítás ──────────────────────────────────
function calcStats() {
  const tips     = history.filter(isFootballAi);
  const won      = tips.filter(t => t.result === "won").length;
  const lost     = tips.filter(t => t.result === "lost").length;
  const push     = tips.filter(t => t.result === "push").length;
  const halfWon  = tips.filter(t => t.result === "half_won").length;
  const halfLost = tips.filter(t => t.result === "half_lost").length;
  const pend     = tips.filter(t => !t.result || t.result === "pending").length;
  const settled  = tips.filter(t => SETTLED.includes(t.result));
  const profit   = settled.reduce((s,t) => s + tipProfit(t), 0);
  // Win% – a fél eredmények fél súllyal, a visszajárók (push) kihagyva
  const decidedW = won + halfWon * 0.5;
  const decidedN = won + lost + halfWon + halfLost;
  const wr        = decidedN ? ((decidedW/decidedN)*100).toFixed(0)+"%" : "–";
  const roi       = settled.length ? ((profit/settled.length)*100).toFixed(1) : "–";
  const profitStr = settled.length ? (profit>=0?"+":"")+profit.toFixed(2) : "–";
  const roiStr    = roi!=="–" ? (profit>=0?"+":"")+roi+"%" : "–";
  return { total: tips.length, won, lost, push, halfWon, halfLost, pend, wr, profitStr, roiStr };
}

// Egy kombi profitja egységben (comboPayout - 1); lezáratlannál 0.
function comboProfit(t) {
  if (!SETTLED.includes(t.result)) return 0;
  const payout = t.comboPayout != null ? t.comboPayout
               : (t.result === "won" ? (parseFloat(t.odds) || 0) : t.result === "push" ? 1 : 0);
  return payout - 1;
}
// ── Kombi statisztika (külön a singlektől) ────────────────
function calcComboStats() {
  const c       = history.filter(t => t.type === "combo");
  const settled = c.filter(t => SETTLED.includes(t.result));
  const won     = c.filter(t => t.result === "won").length;
  const lost    = c.filter(t => t.result === "lost").length;
  const pend    = c.filter(t => !t.result || t.result === "pending").length;
  const profit  = settled.reduce((s,t) => s + comboProfit(t), 0);
  const roi     = settled.length ? ((profit/settled.length)*100).toFixed(1) : "–";
  return { total: c.length, won, lost, pend, settled: settled.length,
    profitStr: settled.length ? (profit>=0?"+":"")+profit.toFixed(2) : "–",
    roiStr: roi!=="–" ? (profit>=0?"+":"")+roi+"%" : "–" };
}

function buildStatsMsg(title) {
  const { total, won, lost, push, halfWon, halfLost, pend, wr, profitStr, roiStr } = calcStats();
  const pushTotal = push + halfWon + halfLost;   // a fél eredmények a visszajárhoz számítanak
  const cs = calcComboStats();
  const comboSection = cs.total ? `\n\n🎰 <b>Kombi tippek</b> <i>(külön)</i>\n`+
    `Nyert/Vesztett: <b>${cs.won}</b> / <b>${cs.lost}</b> (folyamatban: ${cs.pend})\n`+
    `Profit: <b>${cs.profitStr} egység</b> · ROI: <b>${cs.roiStr}</b>` : "";
  return `📈 <b>${title}</b>\n`+
    `📅 ${new Date().toLocaleDateString("hu-HU")}\n\n`+
    `📊 <b>Összesítés</b>\n`+
    `Összes tipp: <b>${total}</b>\n`+
    `⏳ Folyamatban: <b>${pend}</b>\n`+
    `✅ Nyert: <b>${won}</b>\n`+
    `❌ Vesztett: <b>${lost}</b>\n`+
    `↩️ Visszajár: <b>${pushTotal}</b>\n\n`+
    `📉 <b>Teljesítmény</b>\n`+
    `Win %: <b>${wr}</b>\n`+
    `Profit: <b>${profitStr} egység</b>\n`+
    `ROI: <b>${roiStr}</b>`+
    comboSection;
}

// ── AI tippek ─────────────────────────────────────────────
// Visszatér: { singles: [...], comboLegs: [...] }
async function fetchAiTips(matchList, alreadyTipped = []) {
  if (!ANTHROPIC_KEY || !matchList.length) return { singles: [], comboLegs: [] };
  console.log(`AI elemzés: ${matchList.length} meccs`);

  const matchText = matchList.map(m =>
    `- ${m.sport} | ${m.match} | Kezdés: ${m.commence}\n  Valós odds: ${m.odds.map(o => `${o.market} / ${o.name}: ${o.odds} (${o.bookmaker})`).join(", ")}`
  ).join("\n");

  const skipNote = alreadyTipped.length
    ? `\nEZEKRE A MECCSEKRE MÁR VAN TIPP – NE szerepeljen sem SINGLE tippként, sem KOMBI LÁBKÉNT: ${alreadyTipped.join("; ")}\n`
    : "";

  const prompt = `Te egy profi labdarúgás-fogadási elemző vagy. Használj web keresést az aktuális formához, sérülésekhez és keretinformációkhoz az alábbi közelgő foci meccsekre (a következő ~36 óra).

Mai meccsek (valós bookmaker oddsokkal):
${matchText}
${skipNote}
KÉT dolgot adj:

1) "tippek": 2-3 ERŐS single tipp (csak a legjobbak, ne erőltesd a számot).
   - MECCSENKÉNT LEGFELJEBB 1 single tipp – a legerősebb piacot válaszd az adott meccsre. Ne adj több tippet ugyanarra a meccsre!
   - CSAK legalább ${MIN_SINGLE_ODDS} oddsú single tippet adj – az ennél alacsonyabb oddsú kimenetet NE tedd single tippnek (a nagyon alacsony oddsúak a kombi lábak közé valók).
   - Lehetőleg KÜLÖNBÖZŐ meccsekről legyenek. Ha csak 1 meccs van elérhető, akkor csak 1 tippet adj.
   - Csak pozitív kimenetel: over gólok, hendikep győzelem, csapat győzelme. NE adj under tippet a singlekbe.

2) "kombi_labak": 4-6 BIZTONSÁGOS, alacsony kockázatú láb kombi szelvényekhez.
   - MINDEGYIK láb MÁS meccsről legyen – használj annyi különböző meccset, amennyi elérhető (legalább 2, hogy összeálljon egy kötés; ha van elég meccs, adj 4-6 lábat, hogy több, NEM átfedő kötés is kijöjjön).
   - Ezek külön-külön NEM elég értékesek single tippnek (alacsony odds, jellemzően 1.15-1.55), de kombinálva szép össz oddsot adnak.
   - Magas valószínűségű kimenetelek: erős favorit győzelme, Over 1.5, Under 4.5, hendikep -1 / -1.5 nagy favoritnál stb.

KÖZÖS szabályok:
- Az "odds" mezőbe CSAK a fent megadott valós bookmaker oddsok egyikét írd (a megfelelő piac/kimenet oddsát).
- A "market" és "pick" pontosan egyezzen egy valós piaccal/kimenettel; a csapatnév a fent megadott formában szerepeljen.
- Rövid (1-2 mondat) magyar indoklás valós adatok alapján (csak a "tippek"-hez kell note).

Válaszolj KIZÁRÓLAG egy JSON OBJEKTUMMAL, semmi más szöveg nélkül:
{"tippek":[{"match":"...","sport":"soccer","sportLabel":"⚽ FIFA VB 2026","commence":"07.05 20:00","market":"1X2","pick":"...","odds":1.85,"note":"..."}],"kombi_labak":[{"match":"...","sportLabel":"⚽ FIFA VB 2026","commence":"07.05 20:00","market":"Over 1.5","pick":"Over 1.5","odds":1.28}]}`;

  try {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01" },
      body: JSON.stringify({
        model: "claude-sonnet-4-6", max_tokens: 8000,
        tools: [{ type: "web_search_20250305", name: "web_search" }],
        messages: [{ role: "user", content: prompt }]
      })
    });
    const data = await r.json();
    if (data.error) { console.error("AI API hiba:", JSON.stringify(data.error)); return { singles: [], comboLegs: [] }; }
    const text = (data.content?.filter(b => b.type === "text").map(b => b.text) || []).join("\n");
    if (!text.trim()) { console.log("AI: üres szöveges válasz. stop_reason:", data.stop_reason); return { singles: [], comboLegs: [] }; }

    // JSON kinyerés: 1) ```json...``` blokk, 2) nyers {} blokk
    let jsonStr = null;
    const codeBlock = text.match(/```(?:json)?\s*([\s\S]*?)```/);
    if (codeBlock) {
      jsonStr = codeBlock[1].trim();
    } else {
      // Legelső { és legutolsó } közötti rész
      const first = text.indexOf("{");
      const last  = text.lastIndexOf("}");
      if (first !== -1 && last > first) jsonStr = text.slice(first, last + 1);
    }
    if (!jsonStr) {
      console.log("AI: nem sikerült JSON-t kinyerni. Válasz eleje:\n" + text.slice(0, 500));
      return { singles: [], comboLegs: [] };
    }
    let obj;
    try { obj = JSON.parse(jsonStr); } catch(e) {
      console.log("AI: JSON parse hiba –", e.message, "\nPróbált JSON eleje:\n" + jsonStr.slice(0, 400));
      return { singles: [], comboLegs: [] };
    }
    // A valós kezdési idő a meccslistából (odds API), nem az AI adatából
    const realCommence = name => {
      const exact = matchList.find(m => m.match === name);
      if (exact) return exact.commence;
      const parts = String(name || "").split(/\s+vs\.?\s+/i);
      if (parts.length !== 2) return null;
      const nh = normTeam(parts[0]), na = normTeam(parts[1]);
      const m = matchList.find(x => {
        const p = String(x.match).split(/\s+vs\.?\s+/i);
        if (p.length !== 2) return false;
        const h = normTeam(p[0]), a = normTeam(p[1]);
        return (nameSim(nh, h) && nameSim(na, a)) || (nameSim(nh, a) && nameSim(na, h));
      });
      return m ? m.commence : null;
    };
    const singlesAll = (Array.isArray(obj.tippek) ? obj.tippek : []).map(t => ({
      id: `ai-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      type: "ai", sport: t.sport, sportLabel: t.sportLabel,
      match: t.match, commence: realCommence(t.match) || t.commence || null,
      market: t.market, pick: t.pick, odds: t.odds,
      live: false, note: t.note,
      approved: false, sent: false,
      addedAt: nowHu(), result: "pending"
    }));
    // Backstop: minimum odds szűrő + meccsenként legfeljebb 1 single (az AI a legerősebbet teszi előre)
    const seenMatch = new Set();
    const singles = singlesAll
      .filter(t => (parseFloat(t.odds) || 0) >= MIN_SINGLE_ODDS)
      .filter(t => { if (seenMatch.has(t.match)) return false; seenMatch.add(t.match); return true; });
    const comboLegs = (Array.isArray(obj.kombi_labak) ? obj.kombi_labak : []).map(l => ({
      match: l.match, sportLabel: l.sportLabel || "⚽",
      market: l.market, pick: l.pick, odds: parseFloat(l.odds) || 0, commence: l.commence || null
    })).filter(l => l.match && l.market && l.pick && l.odds > 1);
    return { singles, comboLegs };
  } catch (e) { console.error("AI tipp hiba:", e.message); return { singles: [], comboLegs: [] }; }
}

function comboHash(s) { let h = 5381; for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0; return h.toString(36); }
// Egy kombi láb-halmaz kulcsa (sorrendtől független) – ez alapján dedupolunk.
function comboKey(c) { return (c.legs || []).map(l => `${l.match}|${l.market}|${l.pick}`).sort().join("__"); }
// ── Kombi tippek (csak az izgalom kedvéért) ───────────────
// Az AI biztonságos lábaiból NEM ÁTFEDŐ (diszjunkt) 2-3 lábas kötéseket állít össze:
// két kombi SOSE osztozik lábon (különben korreláltak lennének – ha az egyik veszít,
// a másik sem nyerhetne). Minden láb önállóan, a meccs eredménye alapján dől el.
function buildCombos(legs, matchList = []) {
  // A valós kezdési idő a meccslistából (odds API), nem az AI adatából – laza névpárosítással.
  const realCommence = name => {
    const exact = matchList.find(m => m.match === name);
    if (exact) return exact.commence;
    const parts = String(name || "").split(/\s+vs\.?\s+/i);
    if (parts.length !== 2) return null;
    const nh = normTeam(parts[0]), na = normTeam(parts[1]);
    const m = matchList.find(x => {
      const p = String(x.match).split(/\s+vs\.?\s+/i);
      if (p.length !== 2) return false;
      const h = normTeam(p[0]), a = normTeam(p[1]);
      return (nameSim(nh, h) && nameSim(na, a)) || (nameSim(nh, a) && nameSim(na, h));
    });
    return m ? m.commence : null;
  };
  const byMatch = {};
  for (const l of legs) {
    if (!l.match || !l.odds) continue;
    if (!byMatch[l.match] || l.odds < byMatch[l.match].odds) byMatch[l.match] = l;   // meccsenként a legbiztosabb
  }
  const pool = Object.values(byMatch).sort((a, b) => a.odds - b.odds);   // legbiztosabb elöl

  // A poolt diszjunkt darabokra bontjuk (2-3 láb/darab), egy-1-leftover nélkül.
  const chunks = [];
  let i = 0, remaining = pool.length;
  while (remaining >= 2) {
    const size = remaining === 4 ? 2 : (remaining >= 3 ? 3 : 2);   // 4 → 2+2 (ne maradjon 1 árván)
    chunks.push(pool.slice(i, i + size));
    i += size; remaining -= size;
    if (chunks.length >= 3) break;   // legfeljebb 3 kombi egy futásból
  }

  const mk = items => {
    const n = items.length;
    const legsArr = items.map(l => ({
      match: l.match, sportLabel: l.sportLabel, market: l.market,
      pick: l.pick, odds: l.odds, commence: realCommence(l.match) || l.commence || null, result: null
    }));
    const odds = parseFloat(legsArr.reduce((p, l) => p * l.odds, 1).toFixed(2));
    const id   = "combo-" + n + "-" + comboHash(comboKey({ legs: legsArr }));
    return {
      id, type: "combo", legN: n, legs: legsArr, odds, comboPayout: null,
      note: `${n} lábas kötés`,
      approved: false, sent: false,
      addedAt: nowHu(), result: "pending"
    };
  };
  return chunks.map(mk);
}

// Régi tippeknek nincs approved mezőjük → azokat jóváhagyottnak tekintjük (visszafelé kompatibilitás).
const isApproved = t => t.approved !== false;

// ── Fő frissítő ───────────────────────────────────────────
async function fetchAndProcess() {
  const now   = new Date();
  console.log(`Elemzés indul: ${new Date().toLocaleString("hu-HU", { timeZone: "Europe/Budapest" })}`);

  // Minden MÉG LE NEM ZÁRT (pending) single tipp meccse – dátumtól függetlenül.
  // Így egy előre (pl. tegnap) felvett, még el nem kezdődött meccsre nem ad újabb tippet.
  const tippedMatches = new Set(
    history.filter(t => t.type === "ai" && (!t.result || t.result === "pending")).map(t => t.match)
  );

  const matchList = [];
  let scannedLeagues = 0, oddsCalls = 0;
  for (const [sportKey, meta] of Object.entries(SPORT_MAP)) {
    try {
      // 1) INGYENES esemény-lekérdezés (/events = 0 kredit): van-e meccs a köv. 24 órában?
      const er = await fetch(`https://api.the-odds-api.com/v4/sports/${sportKey}/events?apiKey=${ODDS_API_KEY}&dateFormat=iso`);
      scannedLeagues++;
      if (!er.ok) continue;
      const events = await er.json();
      const hasUpcoming = (Array.isArray(events) ? events : []).some(e => {
        const h = (new Date(e.commence_time) - now) / 3600000;
        return h >= 0 && h <= WINDOW_HOURS;
      });
      if (!hasUpcoming) continue;   // nincs közelgő meccs → NEM kérünk (drága) oddsot

      // 2) Csak most kérünk oddsot (3 kredit/liga), mert van közelgő meccs
      const url   = `https://api.the-odds-api.com/v4/sports/${sportKey}/odds/?apiKey=${ODDS_API_KEY}&regions=eu&markets=h2h,totals,spreads&oddsFormat=decimal&dateFormat=iso`;
      const r     = await fetch(url);
      oddsCalls++;
      if (!r.ok) continue;
      const games = await r.json();
      for (const game of games) {
        const hoursUntil = (new Date(game.commence_time) - now) / 3600000;
        if (hoursUntil < 0 || hoursUntil > WINDOW_HOURS) continue;
        // (Nincs meccs-kihagyás: a teljes lista kell a kombi lábakhoz is; a single
        //  duplikátumot a prompt + a válasz utólagos szűrése kezeli.)

        const validBMs = (game.bookmakers || []).filter(bm => !EXCLUDED_BM.includes(bm.key) && bm.markets?.length > 0);
        if (validBMs.length < 2) continue;

        const h2hOdds = [];
        const h2hBMs  = validBMs.filter(bm => bm.markets.find(m => m.key === "h2h"));
        if (h2hBMs.length) {
          const names = h2hBMs[0].markets.find(m => m.key === "h2h").outcomes.map(o => o.name);
          names.forEach(name => {
            let best = 0, bestBM = "";
            for (const bm of h2hBMs) {
              const o = bm.markets.find(m => m.key === "h2h")?.outcomes?.find(x => x.name === name);
              if (o && o.price > best) { best = o.price; bestBM = bm.title; }
            }
            if (best) h2hOdds.push({ market: "1X2", name, odds: parseFloat(best.toFixed(2)), bookmaker: bestBM });
          });
        }

        const totalsOdds = [];
        const totalsBMs  = validBMs.filter(bm => bm.markets.find(m => m.key === "totals"));
        if (totalsBMs.length) {
          const best = {};
          for (const bm of totalsBMs) {
            for (const o of bm.markets.find(m => m.key === "totals")?.outcomes || []) {
              if (o.name !== "Over") continue;
              if (!best[o.point] || o.price > best[o.point].odds)
                best[o.point] = { market: `Over ${o.point}`, name: `Over ${o.point}`, odds: parseFloat(o.price.toFixed(2)), bookmaker: bm.title };
            }
          }
          totalsOdds.push(...Object.values(best));
        }

        const spreadsOdds = [];
        const spreadsBMs  = validBMs.filter(bm => bm.markets.find(m => m.key === "spreads"));
        if (spreadsBMs.length) {
          const best = {};
          for (const bm of spreadsBMs) {
            for (const o of bm.markets.find(m => m.key === "spreads")?.outcomes || []) {
              const key = `${o.name}_${o.point}`;
              if (!best[key] || o.price > best[key].odds)
                best[key] = { market: `Hendikep ${o.point > 0 ? "+" : ""}${o.point}`, name: `${o.name} ${o.point > 0 ? "+" : ""}${o.point}`, odds: parseFloat(o.price.toFixed(2)), bookmaker: bm.title };
            }
          }
          spreadsOdds.push(...Object.values(best));
        }

        const allOdds = [...h2hOdds, ...totalsOdds, ...spreadsOdds];
        if (allOdds.length) matchList.push({ sport: meta.label, match: `${game.home_team} vs ${game.away_team}`, commence: huTime(game.commence_time), odds: allOdds });
      }
    } catch {}
  }
  console.log(`Ligák átnézve (ingyenes): ${scannedLeagues} · odds-hívás (fizetős, ~3 kredit/liga): ${oddsCalls} · feldolgozható meccs: ${matchList.length}`);

  const { singles, comboLegs } = await fetchAiTips(matchList, [...tippedMatches]);

  // Backstop: a már ma tippelt meccsekre ne kerüljön újabb SINGLE (a prompt mellett is szűrünk)
  const newAiTips = singles.filter(t => !tippedMatches.has(t.match));

  // Új single tippek hozzáadása a history-hoz (a meglévők megtartásával)
  const existingIds = new Set(history.map(t => t.id));
  const fresh = newAiTips.filter(t => !existingIds.has(t.id));
  if (fresh.length) { history = [...fresh, ...history]; saveHistory(); }
  saveLastRun();

  // A főoldal MINDEN még le nem zárt (pending) AI tippet mutasson (a korábbi futásokét is).
  aiTips = history.filter(t => t.type === "ai" && (!t.result || t.result === "pending"));

  // Kombi tippek (külön, csak az izgalom kedvéért) – az AI biztonságos lábaiból.
  // Új single nélkül is jöhet friss kombi, de a KORÁBBIVAL azonos láb-halmazú NEM
  // duplikálódik (a dedup a lábakat nézi, nem az azonosítót).
  const existingKeys = new Set(history.filter(t => t.type === "combo").map(comboKey));
  // Backstop: ha egy kombi láb meccse már single tippként szerepel (ma, pending), kiszűrjük
  const filteredComboLegs = comboLegs.filter(l => !tippedMatches.has(l.match));
  const freshCombos = buildCombos(filteredComboLegs, matchList).filter(c => !existingKeys.has(comboKey(c)));
  if (freshCombos.length) { history = [...freshCombos, ...history]; saveHistory(); }
  comboTips = history.filter(t => t.type === "combo" && (!t.result || t.result === "pending"));

  // Státusz-értesítés Telegramra (a tippek TARTALMA NEM megy ki – az csak jóváhagyás után,
  // a "📤 Jóváhagyottak küldése" gombbal). Ez csak egy heads-up, hogy lefutott a lekérdezés.
  const total = fresh.length + freshCombos.length;
  console.log(`Frissítve – ${fresh.length} új AI tipp, ${freshCombos.length} új kombi (jóváhagyásra várnak)`);
}

// ── football-data.org: 90 perces (rendes idejű) eredmény ──
// A fogadások a rendes játékidőre dőlnek el (90' + hosszabbítás[stoppage], de
// hosszabbítás/tizenegyes nélkül). Az odds API a hosszabbítással együtti végeredményt
// adja, ami kieséses meccseknél hibás. A football-data.org score.regularTime a 90 perces
// eredmény – ha be van állítva a FOOTBALLDATA_TOKEN, ezt használjuk a kiértékeléshez.
function normTeam(s) {
  return (s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/\b(fc|cf|sc|afc|cd|ac|ss|ssc|as|rc|fk|sk|club|deportivo|united|city)\b/g, "")
    .replace(/[^a-z0-9]/g, "");
}
function levDist(a, b) {
  const m = a.length, n = b.length, d = [];
  for (let i = 0; i <= m; i++) d[i] = [i];
  for (let j = 0; j <= n; j++) d[0][j] = j;
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      d[i][j] = Math.min(d[i-1][j]+1, d[i][j-1]+1, d[i-1][j-1] + (a[i-1] === b[j-1] ? 0 : 1));
  return d[m][n];
}
function nameSim(a, b) {
  if (!a || !b) return false;
  if (a === b || a.includes(b) || b.includes(a)) return true;
  return levDist(a, b) <= Math.max(2, Math.floor(Math.min(a.length, b.length) * 0.25));
}
function fdTeamNames(t) {
  return [t?.name, t?.shortName, t?.tla].filter(Boolean).map(normTeam);
}
function fdMatchFixture(game, matches) {
  const kt = new Date(game.commence_time).getTime();
  const nh = normTeam(game.home_team), na = normTeam(game.away_team);
  for (const m of matches) {
    const fkt = new Date(m.utcDate || 0).getTime();
    if (Math.abs(fkt - kt) > 20 * 60000) continue;                 // ±20 perc a kezdéshez horgony
    const homeOk = fdTeamNames(m.homeTeam).some(x => nameSim(nh, x));
    const awayOk = fdTeamNames(m.awayTeam).some(x => nameSim(na, x));
    if (homeOk && awayOk) return m;                                // hazai↔hazai, vendég↔vendég
  }
  return null;
}
// 90 perces eredmény kinyerése: regularTime ha van, különben fullTime
// (REGULAR meccsnél a fullTime = 90 perc). A mezőnevek eltérhetnek (home/away vagy homeTeam/awayTeam).
function fdScore90(m) {
  const pick = o => o && (o.home != null || o.homeTeam != null)
    ? { home: +(o.home ?? o.homeTeam), away: +(o.away ?? o.awayTeam) } : null;
  const s = m.score || {};
  const duration = s.duration || "REGULAR";
  const reg = pick(s.regularTime);
  if (reg) return reg;                                       // regularTime mindig helyes
  if (duration === "EXTRA_TIME" || duration === "PENALTY_SHOOTOUT") {
    // Hosszabbítás/büntetők esetén fullTime ≠ 90 perc → NE használjuk visszaesésként
    console.log(`    ⚠️  KO-meccs (${duration}), de nincs regularTime adat – kiértékelés halasztva: ${m.homeTeam?.shortName || ""} vs ${m.awayTeam?.shortName || ""}`);
    return null;
  }
  return pick(s.fullTime);                                   // REGULAR meccsnél fullTime = 90 perc
}
async function fdMatchesForDate(dateStr, cache) {
  if (!FOOTBALLDATA_TOKEN) return null;
  if (dateStr in cache) return cache[dateStr];
  const to = new Date(new Date(dateStr + "T00:00:00Z").getTime() + 86400000).toISOString().slice(0, 10);
  const url = `https://api.football-data.org/v4/matches?dateFrom=${dateStr}&dateTo=${to}`;   // 1 napos ablak (ingyenes csomag ezt engedi)
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const r = await fetch(url, { headers: { "X-Auth-Token": FOOTBALLDATA_TOKEN } });
      const body = await r.text();
      if (r.ok) {
        const list = Array.isArray(JSON.parse(body).matches) ? JSON.parse(body).matches : [];
        console.log(`football-data.org ${dateStr}: ${list.length} meccs`);
        cache[dateStr] = list;
        return list;
      }
      if (attempt === 1) { await new Promise(res => setTimeout(res, 1500)); continue; }   // átmeneti hiba → 1 újrapróba
      console.log(`football-data.org HTTP ${r.status} (${dateStr}): ${body.slice(0, 150)}`);
    } catch (e) {
      if (attempt === 1) { await new Promise(res => setTimeout(res, 1500)); continue; }
      console.error(`football-data.org hiba (${dateStr}):`, e.message);
    }
  }
  cache[dateStr] = null;
  return null;
}
// Visszatér: { home, away, status } a 90 perces eredménnyel, vagy null ha nincs megbízható párosítás.
async function regulationScore(game, cache) {
  if (!FOOTBALLDATA_TOKEN) return null;
  const base = new Date(game.commence_time);
  const dates = [base.toISOString().slice(0, 10)];
  const nxt = new Date(base.getTime() + 6 * 3600000).toISOString().slice(0, 10);
  if (nxt !== dates[0]) dates.push(nxt);                            // UTC éjfélen átnyúló kezdés
  for (const d of dates) {
    const fx = await fdMatchesForDate(d, cache);
    if (!fx) continue;
    const m = fdMatchFixture(game, fx);
    if (m && m.status === "FINISHED") {
      const sc = fdScore90(m);
      const dur = m.score?.duration || "REGULAR";
      if (sc && sc.home != null) {
        if (dur === "EXTRA_TIME" || dur === "PENALTY_SHOOTOUT") {
          console.log(`    ℹ️  KO-meccs (${dur}): 90 perces eredmény = ${sc.home}-${sc.away} (nem a végeredmény!)`);
        }
        return { home: sc.home, away: sc.away, status: dur };
      }
    }
  }
  return null;
}

// Egy piac kiértékelése a 90 perces eredmény alapján. Visszatér:
// won / lost / push / half_won / half_lost, vagy null ha nem értelmezhető.
function settleMarket(market, pick, homeTeam, awayTeam, homeScore, awayScore) {
  const mk = (market || "").toLowerCase();
  // A pick csapatneve az AI-tól jön ("Molde"), a valós név az odds API-tól ("Molde FK") –
  // ezért laza (normalizált) névegyezést használunk, nem szigorú ===-t.
  const pickIsHome = () => nameSim(normTeam(pick), normTeam(homeTeam));
  const pickIsAway = () => nameSim(normTeam(pick), normTeam(awayTeam));
  if (market === "1X2") {
    // Három kimenet: csapattippnél döntetlen = vereség (push nincs).
    if (/^draw$|^döntetlen$|^x$/i.test((pick || "").trim())) return homeScore === awayScore ? "won" : "lost";
    if (pickIsHome()) return homeScore >  awayScore ? "won" : "lost";
    if (pickIsAway()) return awayScore >  homeScore ? "won" : "lost";
    return null;
  }
  if (mk.includes("over")) {
    const line = parseFloat(market.match(/[\d.]+/)?.[0] || 0);
    return settleQuarter(homeScore + awayScore, line);            // ázsiai over is (2.75 stb.)
  }
  if (mk.includes("under")) {
    const line = parseFloat(market.match(/[\d.]+/)?.[0] || 0);
    const r = settleQuarter(homeScore + awayScore, line);         // Under = az Over ellentettje
    return r === "won" ? "lost" : r === "lost" ? "won"
         : r === "half_won" ? "half_lost" : r === "half_lost" ? "half_won" : r;
  }
  if (mk.includes("hendikep") || mk.includes("handicap")) {
    const lineMatch = (pick || "").match(/-?\+?[\d.]+$/);
    if (!lineMatch) return null;
    const h      = parseFloat(lineMatch[0].replace("+", ""));      // pl. +0.75 / -1.5
    const team   = (pick || "").replace(/[-+][\d.]+$/, "").trim();  // a csapatnév a hendikep előtt
    const isHome = nameSim(normTeam(team), normTeam(homeTeam));
    const d      = isHome ? homeScore - awayScore : awayScore - homeScore;
    return settleQuarter(d, -h);
  }
  if (mk.includes("btts") || mk.includes("mindkét")) {
    return homeScore > 0 && awayScore > 0 ? "won" : "lost";
  }
  return null;
}

// ── Eredményjelölés ───────────────────────────────────────
async function checkResults() {
  const pendingSingles = history.filter(t => t.type === "ai"    && (t.result === "pending" || !t.manual));
  // Kombik: a nyitottak ÉS azok, amelyek már lezártak, de van még kitöltetlen lábuk
  // (korai vesztes esetén a hátralévő lábakat a későbbi futások töltik ki).
  const combosToCheck = history.filter(t => t.type === "combo" &&
    (!t.result || t.result === "pending" || (t.legs || []).some(l => !l.result)));
  if (!pendingSingles.length && !combosToCheck.length) return;
  console.log(`Eredmények ellenőrzése (${pendingSingles.length} single, ${combosToCheck.length} kombi)...`);
  let changed = false;
  const fdCache = {};

  // 1. Befejezett meccsek 90 perces eredményének összegyűjtése (meccsnév → eredmény)
  // Csak azokat a sportokat kérdezzük le, amelyekhez ténylegesen van pending tipp → kredit takarékosság
  const allPending = [...pendingSingles, ...combosToCheck.flatMap(c => c.legs || [])];
  const neededSports = new Set(allPending.map(t => t.sportKey).filter(Boolean));
  // Ha nincs sportKey a tippeknél, fallback: összes sport
  const sportsToCheck = neededSports.size > 0 ? [...neededSports] : Object.keys(SPORT_MAP);
  console.log(`  Odds API lekérés: ${sportsToCheck.length} sport (${sportsToCheck.join(", ")})`);
  const completed = {};
  for (const sportKey of sportsToCheck) {
    try {
      const r = await fetch(`https://api.the-odds-api.com/v4/sports/${sportKey}/scores/?apiKey=${ODDS_API_KEY}&daysFrom=3`);
      if (!r.ok) continue;
      const games = await r.json();
      for (const game of games) {
        if (!game.completed || !game.scores) continue;
        const matchName = `${game.home_team} vs ${game.away_team}`;
        let homeScore = parseInt(game.scores.find(s => s.name === game.home_team)?.score || 0);
        let awayScore = parseInt(game.scores.find(s => s.name === game.away_team)?.score || 0);
        let src = "odds";
        const reg = await regulationScore(game, fdCache);
        if (reg) { homeScore = reg.home; awayScore = reg.away; src = `90'(${reg.status})`; }
        completed[matchName] = { home_team: game.home_team, away_team: game.away_team, homeScore, awayScore, id: game.id, src };
      }
    } catch (e) { console.error(`Scores hiba (${sportKey}):`, e.message); }
  }

  // A tippek "match" neve az AI-tól jön, a completed kulcsai az odds API-tól – ezek eltérhetnek
  // (pl. "Tromso vs Vålerenga" vs "Tromsø IL vs Vålerenga Fotball"), sőt az AI a hazai/vendég
  // sorrendet is felcserélheti ("Molde vs Aalesund" a valós "Aalesund vs Molde" helyett).
  // Ezért: pontos egyezés → normalizált egyezés → FORDÍTOTT sorrendű egyezés.
  // A kiértékelés mindig a valós (odds API-s) hazai/vendég csapatot használja, így a
  // fordított párosítás sem torzítja az eredményt.
  const completedList = Object.entries(completed);
  const findByName = name => {
    if (completed[name]) return completed[name];                     // pontos egyezés
    const parts = String(name || "").split(/\s+vs\.?\s+/i);
    if (parts.length !== 2) return null;
    const nh = normTeam(parts[0]), na = normTeam(parts[1]);
    for (const [, g] of completedList) {                             // normalizált egyezés
      if (nameSim(nh, normTeam(g.home_team)) && nameSim(na, normTeam(g.away_team))) return g;
    }
    for (const [, g] of completedList) {                             // fordított hazai/vendég
      if (nameSim(nh, normTeam(g.away_team)) && nameSim(na, normTeam(g.home_team))) {
        console.log(`    ⇄ Fordított névsorrend: "${name}" → ${g.home_team} vs ${g.away_team}`);
        return g;
      }
    }
    return null;
  };
  const findGame = tip => findByName(tip.match) || Object.values(completed).find(g => g.id === tip.matchId);

  // 2. Single tippek kiértékelése
  for (const tip of pendingSingles) {
    const g = findGame(tip);
    if (!g) continue;
    const result = settleMarket(tip.market, tip.pick, g.home_team, g.away_team, g.homeScore, g.awayScore);
    if (!result) { console.log(`    ✗ ${tip.market}/${tip.pick} – nem értékelhető`); continue; }
    if (tip.result === result && tip.homeScore != null) continue;
    const fix = tip.result && tip.result !== "pending" && tip.result !== result ? " (JAVÍTÁS)" : "";
    console.log(`  ${g.home_team} vs ${g.away_team}: ${g.homeScore}-${g.awayScore} [${g.src}] · ${tip.pick} → ${result}${fix}`);
    const patch = { result, homeScore: g.homeScore, awayScore: g.awayScore, settledAt: nowHu() };
    history = history.map(t => t.id === tip.id ? { ...t, ...patch } : t);
    aiTips  = aiTips.map(t => t.id === tip.id ? { ...t, ...patch } : t);
    changed = true;
  }

  // 3. Kombik – MINDEN láb önállóan a meccs eredménye alapján (laza névpárosítással, lásd fent).
  // KORAI VESZTES: ha akár EGY láb már vesztett, a kombi matematikailag nem nyerhet → azonnal
  // "lost" (bekerül a statisztikába), a hátralévő lábakat pedig a későbbi futások töltik ki.
  for (const combo of combosToCheck) {
    const legGames = combo.legs.map(leg => findByName(leg.match));
    const legResFresh = [];  // csak az aktuális futás fresh eredményei
    const legRes = combo.legs.map((leg, i) => {
      const g = legGames[i];
      // Ha a meccs kezdési ideje még nem jött el (>0 óra van hátra), ne értékeljük ki
      if (leg.commence) {
        const commenceHu = leg.commence.replace(/^(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})$/, "2026-$1-$2T$3:$4:00");
        const hoursUntil = (new Date(commenceHu) - Date.now()) / 3600000;
        if (hoursUntil > 0.5) {
          console.log(`    Láb kihagyva (jövőbeli, ${hoursUntil.toFixed(1)}h): ${leg.match}`);
          legResFresh.push(null);
          return null;  // jövőbeli meccsnek nem lehet érvényes eredménye
        }
      }
      const fresh = g ? settleMarket(leg.market, leg.pick, g.home_team, g.away_team, g.homeScore, g.awayScore) : null;
      const res = fresh || leg.result || null;
      if (res) console.log(`    Láb eredmény: ${leg.match} → ${res} (fresh:${fresh}, g:${!!g}, commence:${leg.commence})`);
      legResFresh.push(fresh);  // aktuális evaluáció eredménye
      return res;
    });
    const legs      = combo.legs.map((l, i) => ({ ...l, result: legRes[i] }));
    const legsFilled = JSON.stringify(legs) !== JSON.stringify(combo.legs);
    // Csak akkor lost, ha az aktuális futásban megtalált meccset értékeltük ki vesztesnek
    const anyLost   = legRes.some((r, i) => r === "lost" && legGames[i] !== null);
    const allKnown  = legRes.every(r => !!r);

    if (anyLost) {
      // A kombi bukott – akkor is, ha más lábak még nyitottak.
      const patch = { result: "lost", comboPayout: 0, legs, settledAt: combo.settledAt || nowHu() };
      if (combo.result !== "lost" || legsFilled) {
        history   = history.map(t => t.id === combo.id ? { ...t, ...patch } : t);
        comboTips = comboTips.map(t => t.id === combo.id ? { ...t, ...patch } : t);
        changed = true;
        const open = legs.filter(l => !l.result).length;
        if (combo.result !== "lost")
          console.log(`  KOMBI (${combo.legN} lábas) → lost (vesztes láb${open ? `, ${open} láb még nyitott` : ""})`);
      }
      continue;
    }

    if (!allKnown) {
      const open = combo.legs.filter((l, i) => !legRes[i])
        .map((l, i) => `${l.match}${legGames[combo.legs.indexOf(l)] ? " (piac nem értékelhető: " + l.market + "/" + l.pick + ")" : " (meccs még nincs lezárva)"}`);
      if (legsFilled) {   // részeredmények mentése (megjelenítéshez)
        history   = history.map(t => t.id === combo.id ? { ...t, legs } : t);
        comboTips = comboTips.map(t => t.id === combo.id ? { ...t, legs } : t);
        changed = true;
      }
      console.log(`  KOMBI (${combo.legN} lábas) – még nyitott: ${open.join("; ")}`);
      continue;
    }

    // Minden láb ismert és egyik sem vesztett → tényleges kifizetés
    const mults = legRes.map((r, i) => {
      const o = parseFloat(combo.legs[i].odds) || 0;
      switch (r) {
        case "won":       return o;
        case "half_won":  return (1 + o) / 2;
        case "push":      return 1;
        case "half_lost": return 0.5;
        default:          return 0;
      }
    });
    const payout = mults.reduce((a, b) => a * b, 1);
    const result = payout > 1.0001 ? "won" : payout < 0.9999 ? "lost" : "push";
    const patch  = { result, comboPayout: parseFloat(payout.toFixed(2)), legs, settledAt: nowHu() };
    if (combo.result !== result || combo.comboPayout !== patch.comboPayout || legsFilled) {
      history   = history.map(t => t.id === combo.id ? { ...t, ...patch } : t);
      comboTips = comboTips.map(t => t.id === combo.id ? { ...t, ...patch } : t);
      changed = true;
      console.log(`  KOMBI (${combo.legN} lábas) → ${result} (x${patch.comboPayout})`);
    }
  }

  if (changed) { saveHistory(); console.log("Eredmények mentve ✓"); }
  else console.log("Nincs új lezárt meccs.");
}


// ── Ütemező ───────────────────────────────────────────────
function scheduleNextFetch() {
  const { hour, minute, day } = getHungarianTime();
  const isWeekend  = day === 0 || day === 5 || day === 6;
  const fetchHours = isWeekend ? [10, 15, 19] : [15];
  let minsUntilNext = null;
  for (const h of fetchHours) {
    const diff = (h - hour) * 60 - minute;
    if (diff > 0) { minsUntilNext = diff; break; }
  }
  if (!minsUntilNext) {
    const tomorrowDay = (day + 1) % 7;
    const isWE        = tomorrowDay === 0 || tomorrowDay === 5 || tomorrowDay === 6;
    minsUntilNext     = (24 - hour) * 60 - minute + (isWE ? 10 : 15) * 60;
  }
  const h = Math.floor(minsUntilNext / 60), m = minsUntilNext % 60;
  console.log(`Következő lekérés: ${h} óra ${m} perc múlva (magyar idő)`);
  setTimeout(() => { fetchAndProcess(); scheduleNextFetch(); }, minsUntilNext * 60 * 1000);
}


// ── Lejárt előfizetések ellenőrzése ──────────────────────────
function checkExpiredSubscriptions() {
  const now = new Date();
  const expired = usersDb.all().filter(u =>
    u.plan === "pro" &&
    u.paidUntil &&
    new Date(u.paidUntil) < now &&
    !u.isAdmin
  );
  if (!expired.length) return;
  expired.forEach(u => {
    const expiredAt = new Date().toISOString();
    usersDb.update(u.id, { plan: "free", subscriptionStatus: "cancelled", cancelledAt: expiredAt });
    console.log(`Előfizetés lejárt, visszaminősítve: ${u.email} (lejárt: ${u.paidUntil})`);
    mailer.sendSubscriptionExpired(u.email).catch(e => console.error("Email hiba:", e.message));
    const adminEmail = process.env.ADMIN_EMAIL;
    if (adminEmail) mailer.send({
      to: adminEmail,
      subject: `⏰ Előfizetés lejárt – ${u.email}`,
      text: `${u.email} előfizetése lejárt.`,
      html: `<p>A <b>${u.email}</b> felhasználó előfizetése lejárt (${u.paidUntil}).</p>`,
    }).catch(e => console.error("Admin email hiba:", e.message));
  });
  console.log(`Lejárt előfizetések: ${expired.length} felhasználó visszaminősítve.`);
}

// Indításkor azonnal lefut (startup után fogja az esetleg lejártakat kezelni)
checkExpiredSubscriptions();

// ── Napnyitó: 00:03 automatikus eredmény-ellenőrzés, 00:05 napi statisztika Telegramra
//    (magyar idő). Tipptartalom NEM megy ki automatikusan – az csak jóváhagyás után, kézzel. ──
let _lastCheckDay = "", _lastStatsDay = "";
setInterval(async () => {
  const { hour, minute } = getHungarianTime();
  const dayKey = todayHU();                       // naponta egyszer, dupla lefutás ellen
  if (hour === 0 && minute === 1 && _lastCheckDay !== dayKey) {
    checkExpiredSubscriptions();
  }
  // Heti összefoglaló: minden hétfőn 08:00-kor
  if (hour === 8 && minute === 0 && new Date().getDay() === 1) {
    const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    const weekTips = history.filter(t =>
      t.type !== "combo" && t.result && t.result !== "pending" &&
      t.settledAt && new Date(t.settledAt) >= weekAgo
    );
    if (weekTips.length) {
      const won = weekTips.filter(t => t.result === "won").length;
      const lost = weekTips.filter(t => t.result === "lost").length;
      const halfWon = weekTips.filter(t => t.result === "half_won").length;
      const halfLost = weekTips.filter(t => t.result === "half_lost").length;
      const push = weekTips.filter(t => t.result === "push").length;
      const decN = won + lost + halfWon + halfLost;
      const winRate = decN ? (((won + halfWon * 0.5) / decN) * 100).toFixed(1) : "0";
      const profit = weekTips.reduce((s, t) => {
        const o = parseFloat(t.odds) || 1;
        if (t.result === "won") return s + (o - 1);
        if (t.result === "lost") return s - 1;
        if (t.result === "half_won") return s + (o - 1) / 2;
        if (t.result === "half_lost") return s - 0.5;
        return s;
      }, 0);
      const roi = weekTips.length ? ((profit / weekTips.length) * 100).toFixed(1) : "0";
      const stats = { won, lost, push, halfWon, halfLost, profit, roi, winRate, settled: weekTips.length };
      const recipients = usersDb.all().filter(u => !u.isAdmin && u.emailVerified !== false && (u.plan === "pro" || !auth.PAID_MODE));
      console.log(`Heti összefoglaló e-mail: ${recipients.length} felhasználónak`);
      for (const u of recipients) {
        mailer.sendWeeklySummary(u.email, stats).catch(e => console.error(`Heti email hiba (${u.email}):`, e.message));
      }
    }
  }
  if (hour === 0 && minute === 3 && _lastCheckDay !== dayKey) {
    _lastCheckDay = dayKey;
    console.log("Napnyitó automatikus eredmény-ellenőrzés...");
    await checkResults();
  }
  if (hour === 0 && minute === 5 && _lastStatsDay !== dayKey) {
    _lastStatsDay = dayKey;
    await sendTelegram(buildStatsMsg("90perc.hu – Napi statisztika"));
  }
}, 60000);

// ── Admin hitelesítés ─────────────────────────────────────
// Jelszó jöhet: x-admin-password header, body.password, vagy ?password= query.
// Jelszó jöhet: x-admin-password header (ASCII), x-admin-password-b64 header (UTF-8 biztos,
// base64), body.password, vagy ?password= query.
function extractPwd(req) {
  const b64 = req.get("x-admin-password-b64");
  if (b64) { try { return Buffer.from(b64, "base64").toString("utf8"); } catch {} }
  return req.get("x-admin-password") || req.body?.password || req.query?.password || "";
}
function requireAdmin(req, res) {
  // 1) Belépett admin fiók (e-mail + jelszó, session cookie) – ez az elsődleges mód
  if (req.user?.isAdmin) return true;
  // 2) Régi, jelszavas mód (visszafelé kompatibilitás: ?admin=... / header)
  if (!ADMIN_PWD) {
    // Ha nincs jelszó beállítva, nyitva marad – de erről induláskor figyelmeztetünk.
    return true;
  }
  const pwd = extractPwd(req);
  if (pwd !== ADMIN_PWD) {
    res.status(403).json({ error: "Hozzáférés megtagadva — hibás vagy hiányzó admin jelszó." });
    return false;
  }
  return true;
}

// ── Auth végpontok ────────────────────────────────────────
const baseUrl = req => BASE_URL || `${req.protocol}://${req.get("host")}`;

async function sendVerifyEmail(req, user) {
  const token = auth.makePurposeToken("verify", user.id, 24 * 3600 * 1000);
  const url   = `${baseUrl(req)}/api/auth/verify?token=${encodeURIComponent(token)}`;
  return mailer.sendVerification(user.email, url);
}

app.post("/api/auth/register", async (req, res) => {
  const { email, password, acceptTerms, over18 } = req.body || {};
  if (!acceptTerms || !over18) {
    return res.status(400).json({ error: "El kell fogadnod az ÁSZF-et és nyilatkoznod kell a 18. életéved betöltéséről." });
  }
  const r = await usersDb.create(email, password);
  if (!r.ok) return res.status(400).json({ error: r.error });
  usersDb.update(r.user.id, { acceptedTermsAt: new Date().toISOString() });
  auth.setSession(res, r.user.id);
  sendVerifyEmail(req, r.user).catch(() => {});     // ne blokkolja a választ
  console.log(`Új regisztráció: ${r.user.email} (összes: ${usersDb.count()})`);
  res.json({ ok: true, user: usersDb.publicView(r.user), hasAccess: auth.hasAccess(r.user) });
});

// E-mail megerősítés (a levélben lévő linkről érkezik – HTML választ adunk)
app.get("/api/auth/verify", (req, res) => {
  const uid = auth.readPurposeToken("verify", req.query.token);
  const page = (icon, title, msg, color) => `<!DOCTYPE html><html lang="hu"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title></head>
<body style="margin:0;background:#07111d;color:#cfd8dc;font-family:-apple-system,Segoe UI,Roboto,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px">
<div style="background:#0d1b2a;border:1px solid #1e3a2f;border-radius:12px;padding:28px;max-width:420px;text-align:center">
<div style="font-size:40px;margin-bottom:8px">${icon}</div>
<div style="color:${color};font-weight:700;font-size:18px;margin-bottom:8px">${title}</div>
<div style="color:#78909c;font-size:14px;line-height:1.6;margin-bottom:18px">${msg}</div>
<a href="/" style="display:inline-block;background:#00e676;color:#07111d;font-weight:700;padding:11px 22px;border-radius:8px;text-decoration:none">Tovább a tippekhez</a>
</div></body></html>`;
  if (!uid) {
    return res.status(400).send(page("⚠️", "Érvénytelen vagy lejárt link",
      "A megerősítő link lejárt (24 óráig érvényes) vagy érvénytelen. Lépj be, és kérj új megerősítő e-mailt.", "#ffcc80"));
  }
  const u = usersDb.findById(uid);
  if (!u) return res.status(400).send(page("⚠️", "Nincs ilyen fiók", "A fiók már nem létezik.", "#ffcc80"));
  usersDb.update(uid, { emailVerified: true });
  console.log(`E-mail megerősítve: ${u.email}`);
  res.send(page("✅", "E-mail cím megerősítve", "Köszönjük! A fiókod aktív, jó szórakozást.", "#00e676"));
});

app.post("/api/auth/resend-verification", auth.requireLogin, async (req, res) => {
  if (req.user.emailVerified) return res.json({ ok: true, already: true });
  await sendVerifyEmail(req, req.user);
  res.json({ ok: true });
});

// Elfelejtett jelszó – MINDIG ok:true a válasz (nem áruljuk el, létezik-e a fiók)
app.post("/api/auth/forgot", async (req, res) => {
  const u = usersDb.findByEmail(req.body?.email);
  if (u) {
    // A hash a tokenben → a link egyszer használatos (jelszóváltáskor érvénytelenné válik)
    const token = auth.makePurposeToken("reset", u.id, 3600 * 1000, u.passwordHash);
    const url   = `${baseUrl(req)}/reset.html?token=${encodeURIComponent(token)}`;
    await mailer.sendPasswordReset(u.email, url);
    console.log(`Jelszó-visszaállítás kérve: ${u.email}`);
  }
  res.json({ ok: true });
});

app.post("/api/auth/reset", async (req, res) => {
  const { token, newPassword } = req.body || {};
  const uid = auth.readPurposeToken("reset", token, id => usersDb.findById(id)?.passwordHash || "");
  if (!uid) return res.status(400).json({ error: "A link érvénytelen vagy lejárt. Kérj újat." });
  const r = await usersDb.setPassword(uid, newPassword);
  if (!r.ok) return res.status(400).json({ error: r.error });
  const u = usersDb.findById(uid);
  usersDb.update(uid, { emailVerified: true });   // a linket csak az kaphatta meg, akié a postafiók
  auth.setSession(res, uid);                       // egyből be is léptetjük
  console.log(`Jelszó visszaállítva: ${u.email}`);
  res.json({ ok: true });
});

app.post("/api/auth/login", async (req, res) => {
  const { email, password } = req.body || {};
  const u = await usersDb.verify(email, password);
  if (!u) return res.status(401).json({ error: "Hibás e-mail cím vagy jelszó." });
  auth.setSession(res, u.id);
  res.json({ ok: true, user: usersDb.publicView(u), hasAccess: auth.hasAccess(u) });
});

app.post("/api/auth/logout", (req, res) => {
  auth.clearSession(res);
  res.json({ ok: true });
});

// Ki vagyok? (a frontend ezzel dönti el, mit mutasson)
app.get("/api/auth/me", (req, res) => {
  // Friss DB lookup – Stripe webhook utáni plan változás azonnal látszódjon
  const freshUser = req.user ? (usersDb.findById(req.user.id) || req.user) : req.user;
  res.json({
    user:      usersDb.publicView(freshUser),
    hasAccess: auth.hasAccess(freshUser),
    paidMode:  auth.PAID_MODE,
    isAdmin:   !!req.user?.isAdmin || isAdminReq(req),
  });
});

app.post("/api/auth/password", auth.requireLogin, async (req, res) => {
  const { currentPassword, newPassword } = req.body || {};
  const ok = await usersDb.verify(req.user.email, currentPassword);
  if (!ok) return res.status(401).json({ error: "A jelenlegi jelszó hibás." });
  const r = await usersDb.setPassword(req.user.id, newPassword);
  if (!r.ok) return res.status(400).json({ error: r.error });
  res.json({ ok: true });
});

// ── API végpontok ─────────────────────────────────────────
// Admin (helyes jelszóval VAGY admin fiókkal) MINDEN tippet lát (jóváhagyásra várókat is).
function isAdminReq(req) {
  if (req.user?.isAdmin) return true;
  const pwd = extractPwd(req);
  return !!ADMIN_PWD && pwd === ADMIN_PWD;
}

// ÉLŐ TIPPEK – ez a termék: belépés (és fizetős módban aktív előfizetés) kell hozzá.
app.get("/api/tips", (req, res) => {
  const admin = isAdminReq(req);
  const freshUserTips = req.user ? (usersDb.findById(req.user.id) || req.user) : req.user;
  if (!admin && !auth.hasAccess(freshUserTips)) {
    return res.status(req.user ? 402 : 401).json({
      error: req.user ? "Aktív előfizetés szükséges." : "Belépés szükséges a tippek megtekintéséhez.",
      needLogin: !req.user, needSubscription: !!req.user,
      aiTips: [], comboTips: [],
    });
  }
  res.json({
    aiTips:    admin ? aiTips    : aiTips.filter(isApproved),
    comboTips: admin ? comboTips : comboTips.filter(isApproved),
    admin
  });
});

// ELŐZMÉNY – a LEZÁRT tippek publikusak (ez a track record, a bizalom alapja).
// A még függő (élő) tippeket csak a jogosultak látják, különben kiszivárogna a termék.
app.get("/api/history", (req, res) => {
  if (isAdminReq(req)) return res.json(history);
  const approved = history.filter(isApproved);
  const freshUserHist = req.user ? (usersDb.findById(req.user.id) || req.user) : req.user;
  if (auth.hasAccess(freshUserHist)) return res.json(approved);
  const settledOnly = approved.filter(t => t.result && t.result !== "pending");
  res.json(settledOnly);
});

app.get("/api/status", (req, res) => {
  const { hour, minute, day } = getHungarianTime();
  const isWeekend  = day === 0 || day === 5 || day === 6;
  const fetchHours = isWeekend ? [10, 15, 19] : [15];
  let minsUntilNext = null;
  for (const h of fetchHours) {
    const diff = (h - hour) * 60 - minute;
    if (diff > 0) { minsUntilNext = diff; break; }
  }
  if (!minsUntilNext) {
    const tomorrowDay = (day + 1) % 7;
    const isWE        = tomorrowDay === 0 || tomorrowDay === 5 || tomorrowDay === 6;
    minsUntilNext     = (24 - hour) * 60 - minute + (isWE ? 10 : 15) * 60;
  }
  res.json({ aiTipsCount: aiTips.length, lastUpdate: history[0]?.addedAt || null, nextFetchMs: minsUntilNext * 60 * 1000, isWeekend, fetchHours });
});


app.get("/api/public-stats", (req, res) => {
  const isFociSrv = t => /soccer|foci|⚽/i.test((t.sport || "") + " " + (t.sportLabel || ""));
  const H = history.filter(t =>
    t.type !== "combo" && t.type !== "value" &&
    isApproved(t) && isFociSrv(t) &&
    ["won","lost","push","half_won","half_lost"].includes(t.result)
  );
  const won      = H.filter(t => t.result === "won").length;
  const lost     = H.filter(t => t.result === "lost").length;
  const halfWon  = H.filter(t => t.result === "half_won").length;
  const halfLost = H.filter(t => t.result === "half_lost").length;
  const push     = H.filter(t => t.result === "push").length;
  const settled  = H.length;
  const decN     = won + lost + halfWon + halfLost;
  const winRate  = decN ? (((won + halfWon * 0.5) / decN) * 100).toFixed(1) : null;
  const profit   = H.reduce((sum, t) => {
    const o = parseFloat(t.odds) || 1;
    if (t.result === "won")       return sum + (o - 1);
    if (t.result === "lost")      return sum - 1;
    if (t.result === "half_won")  return sum + (o - 1) / 2;
    if (t.result === "half_lost") return sum - 0.5;
    return sum;  // push
  }, 0);
  const roi = settled ? ((profit / settled) * 100).toFixed(1) : null;
  res.json({ settled, won, lost, push, halfWon, halfLost, winRate, profit: parseFloat(profit.toFixed(2)), roi });
});

app.post("/api/refresh", async (req, res) => {
  if (!requireAdmin(req, res)) return;
  await fetchAndProcess();
  res.json({ ok: true, aiTips: aiTips.length });
});

app.patch("/api/history/:id", (req, res) => {
  if (!requireAdmin(req, res)) return;
  const { result, note, comboPayout, odds, legs, pick } = req.body;

  // Note / odds / pick / legs szerkesztés
  if (note !== undefined || odds !== undefined || legs !== undefined || pick !== undefined) {
    const patch = {};
    if (note !== undefined) patch.note = note;
    if (odds !== undefined) patch.odds = parseFloat(odds);
    if (pick !== undefined) patch.pick = pick;
    if (legs !== undefined) {
      patch.legs = legs;
      const totalOdds = legs.reduce((p, l) => p * parseFloat(l.odds || 1), 1);
      patch.totalOdds = parseFloat(totalOdds.toFixed(2));
    }
    const upd = t => t.id === req.params.id ? { ...t, ...patch } : t;
    history = history.map(upd); latestTips = latestTips.map(upd);
    aiTips = aiTips.map(upd); comboTips = comboTips.map(upd); if (typeof freeTips !== "undefined") freeTips = freeTips.map(upd);
    saveHistory();
    console.log(`Szerkesztve: ${req.params.id}${odds ? " odds:"+odds : ""}${note !== undefined ? " note" : ""}`);
    return res.json({ ok: true });
  }

  // Eredmény kézi beállítása
  const validResults = ["won","lost","push","half_won","half_lost","pending"];
  if (!validResults.includes(result)) return res.status(400).json({ error: "Érvénytelen eredmény" });
  const patch = { result, manual: true };
  const upd = t => t.id === req.params.id ? { ...t, ...patch } : t;
  history    = history.map(upd);
  latestTips = latestTips.map(upd);
  aiTips     = aiTips.map(upd);
  comboTips  = comboTips.map(upd);
  if (typeof freeTips !== "undefined" && freeTips) freeTips = freeTips.map(upd);
  // Ha pending-re állítjuk vissza és nincs benne a comboTips-ben, visszaadjuk
  if (result === "pending") {
    const inCombo = comboTips.some(t => t.id === req.params.id);
    if (!inCombo) {
      const fromHistory = history.find(t => t.id === req.params.id);
      if (fromHistory && fromHistory.type === "combo") comboTips.push(fromHistory);
    }
  }
  saveHistory();
  console.log(`Kézi eredmény javítás: ${req.params.id} → ${result}`);
  res.json({ ok: true });
});

app.delete("/api/history", (req, res) => {
  if (!requireAdmin(req, res)) return;
  history    = [];
  latestTips = [];
  aiTips     = [];
  comboTips  = [];
  saveHistory();
  console.log("History törölve ✓");
  res.json({ ok: true });
});

// Egyetlen tipp/kombi törlése azonosító alapján
app.delete("/api/history/:id", (req, res) => {
  if (!requireAdmin(req, res)) return;
  const id = req.params.id;
  const before = history.length;
  history   = history.filter(t => t.id !== id);
  aiTips    = aiTips.filter(t => t.id !== id);
  comboTips = comboTips.filter(t => t.id !== id);
  const removed = before - history.length;
  if (removed) saveHistory();
  console.log(`Tipp törölve (${id}): ${removed} db`);
  res.json({ ok: true, removed });
});

// Admin jelszó ellenőrzése (beviteli mezős belépéshez)
app.post("/api/admin/login", (req, res) => {
  const pwd = extractPwd(req);
  if (!ADMIN_PWD) return res.json({ ok: true, note: "Nincs ADMIN_PASSWORD beállítva – nyitott mód." });
  if (pwd !== ADMIN_PWD) return res.status(403).json({ ok: false, error: "Hibás jelszó." });
  res.json({ ok: true });
});

// Tipp jóváhagyása (ettől lesz publikus)
app.patch("/api/history/:id/approve", (req, res) => {
  if (!requireAdmin(req, res)) return;
  const id = req.params.id;
  const set = t => t.id === id ? { ...t, approved: true } : t;
  history   = history.map(set);
  aiTips    = aiTips.map(set);
  comboTips = comboTips.map(set);
  saveHistory();
  res.json({ ok: true });
});

// Jóváhagyott, még el nem küldött tippek kézi kiküldése Telegramra
app.post("/api/tips/send", async (req, res) => {
  if (!requireAdmin(req, res)) return;
  const singlesToSend = history.filter(t => t.type === "ai"    && isApproved(t) && !t.sent && (!t.result || t.result === "pending"));
  const combosToSend  = history.filter(t => t.type === "combo" && isApproved(t) && !t.sent && (!t.result || t.result === "pending"));
  if (!singlesToSend.length && !combosToSend.length) {
    return res.json({ ok: true, sent: 0, message: "Nincs kiküldendő (jóváhagyott, még el nem küldött) tipp." });
  }
  const sentIds = new Set([...singlesToSend, ...combosToSend].map(t => t.id));
  const mark = t => sentIds.has(t.id) ? { ...t, sent: true } : t;
  history = history.map(mark); aiTips = aiTips.map(mark); comboTips = comboTips.map(mark);
  saveHistory();
  const total = singlesToSend.length + combosToSend.length;
  console.log(`Tippek kiküldve: ${total} (csak e-mail, Telegram nélkül)`);

  // E-mail értesítő az összes aktív előfizetőnek
  const recipients = usersDb.all().filter(u =>
    !u.isAdmin && u.emailVerified !== false &&
    (u.plan === "pro" || !auth.PAID_MODE)
  );
  console.log(`Tip e-mail küldése ${recipients.length} felhasználónak...`);
  for (const u of recipients) {
    mailer.sendNewTips(u.email, singlesToSend, combosToSend)
      .catch(e => console.error(`Tip email hiba (${u.email}):`, e.message));
  }

  res.json({ ok: true, sent: total });
});

app.post("/api/check-results", async (req, res) => {
  if (!requireAdmin(req, res)) return;
  checkExpiredSubscriptions();
  await checkResults();
  res.json({ ok: true });
});

app.post("/api/admin/check-expiry", (req, res) => {
  if (!requireAdmin(req, res)) return;
  checkExpiredSubscriptions();
  res.json({ ok: true });
});

app.post("/api/stats/send", async (req, res) => {
  if (!requireAdmin(req, res)) return;
  await sendTelegram(buildStatsMsg("90perc.hu – Statisztika"));
  res.json({ ok: true });
});

// ── Analyzer history szinkronizáció ──────────────────────
function aPath(uid) {
  const safe = String(uid).replace(/[^a-z0-9]/g, '').slice(0, 32);
  return '/data/ah_' + safe + '.json';
}

app.get("/api/analyzer-history", (req, res) => {
  const uid = req.query.uid;
  if (!uid) return res.json([]);
  try {
    const p = aPath(uid);
    if (!fs.existsSync(p)) return res.json([]);
    res.json(JSON.parse(fs.readFileSync(p, 'utf8')));
  } catch (e) { res.json([]); }
});

app.post("/api/analyzer-history", (req, res) => {
  const { uid, entry } = req.body;
  if (!uid || !entry) return res.status(400).json({ error: 'Hiányzó adat' });
  try {
    const p = aPath(uid);
    const hist = fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : [];
    const newHist = [entry, ...hist].slice(0, 50);
    fs.writeFileSync(p, JSON.stringify(newHist), 'utf8');
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.delete("/api/analyzer-history", (req, res) => {
  const uid = req.query.uid;
  if (!uid) return res.status(400).json({ error: 'Hiányzó uid' });
  try {
    const p = aPath(uid);
    if (fs.existsSync(p)) fs.unlinkSync(p);
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// ── Indítás ───────────────────────────────────────────────
const PORT = process.env.PORT || 3000;
// ── Admin fiók bootstrap ──────────────────────────────────
// Ha be van állítva ADMIN_EMAIL + ADMIN_PASSWORD, létrehozzuk/frissítjük az admin fiókot,
// hogy e-mail+jelszóval is be tudj lépni (nem csak a régi ?admin= módszerrel).
(async () => {
  const email = process.env.ADMIN_EMAIL;
  if (!email || !ADMIN_PWD) return;
  const existing = usersDb.findByEmail(email);
  if (existing) {
    if (!existing.isAdmin) usersDb.update(existing.id, { isAdmin: true });
  } else {
    const r = await usersDb.create(email, ADMIN_PWD, { isAdmin: true, skipPolicy: true });
    if (r.ok) console.log(`✓ Admin fiók létrehozva: ${email}`);
    else console.warn(`⚠️  Admin fiók létrehozása sikertelen: ${r.error}`);
    if (ADMIN_PWD.length < 12) console.warn("⚠️  Az ADMIN_PASSWORD rövid – éles üzemben érdemes hosszabbra cserélni.");
  }
})();

console.log(`Regisztrált felhasználók: ${usersDb.count()} · Fizetős mód: ${auth.PAID_MODE ? "BE" : "KI (ingyenes szakasz)"}`);


app.get("/api/admin/users", (req, res) => {
  if (!requireAdmin(req, res)) return;
  const list = usersDb.all().map(u => ({
    id: u.id,
    email: u.email,
    plan: u.plan || "free",
    emailVerified: u.emailVerified !== false,
    isAdmin: !!u.isAdmin,
    createdAt: u.createdAt || null,
    paidUntil: u.paidUntil || null,
    cancelledAt: u.cancelledAt || null,
    subscriptionStatus: u.subscriptionStatus || null,
    stripeCustomerId: u.stripeCustomerId || null,
  }));
  res.json(list);
});

app.patch("/api/admin/users/:id", (req, res) => {
  if (!requireAdmin(req, res)) return;
  const { plan, paidUntil } = req.body;
  const validPlan = ["free","pro"].includes(plan) ? plan : "free";
  const targetUser = usersDb.findById(req.params.id);
  const subStatus = validPlan === "pro" ? "active" : "cancelled";
  const cancelledAt = validPlan === "free" ? new Date().toISOString() : null;
  usersDb.update(req.params.id, { plan: validPlan, paidUntil, subscriptionStatus: subStatus, currentPeriodEnd: paidUntil || null, ...(cancelledAt ? { cancelledAt } : {}) });
  console.log(`Felhasználó frissítve: ${req.params.id} → plan:${validPlan}`);
  if (targetUser) {
    if (validPlan === "pro") {
      mailer.sendPlanActivated(targetUser.email, paidUntil).catch(e => console.error("Email hiba:", e.message));
    } else if (validPlan === "free" && targetUser.plan === "pro") {
      mailer.sendPlanCancelled(targetUser.email).catch(e => console.error("Email hiba:", e.message));
    }
  }
  res.json({ ok: true });
});

app.delete("/api/admin/users/:id", (req, res) => {
  if (!requireAdmin(req, res)) return;
  usersDb.update(req.params.id, { disabled: true, plan: "free" });
  console.log(`Felhasználó deaktiválva: ${req.params.id}`);
  res.json({ ok: true });
});


// ── Stripe: Checkout Session létrehozása ─────────────────────
app.post("/api/stripe/checkout", auth.requireLogin, async (req, res) => {
  if (!stripe)       return res.status(500).json({ error: "Stripe nincs konfigurálva" });
  if (!STRIPE_PRICE) return res.status(500).json({ error: "STRIPE_PRICE_ID nincs beállítva" });
  try {
    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      payment_method_types: ["card"],
      customer_email: req.user.email,
      line_items: [{ price: STRIPE_PRICE, quantity: 1 }],
      success_url: `${BASE_URL}/success.html?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url:  `${BASE_URL}/elofizetes.html`,
      metadata:    { userId: req.user.id },
      locale:                   "hu",
      allow_promotion_codes:    true,
      billing_address_collection: "required",
    });
    res.json({ url: session.url });
  } catch (err) {
    console.error("Stripe checkout hiba:", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ── Stripe: Ügyfélportál (előfizetés kezelése / lemondás) ─────
app.post("/api/stripe/portal", auth.requireLogin, async (req, res) => {
  if (!stripe) return res.status(500).json({ error: "Stripe nincs konfigurálva" });
  const user = usersDb.findById(req.user.id);
  if (!user?.stripeCustomerId) return res.status(400).json({ error: "Nincs aktív előfizetés" });
  try {
    const portal = await stripe.billingPortal.sessions.create({
      customer:   user.stripeCustomerId,
      return_url: `${BASE_URL}/tippek.html`,
    });
    res.json({ url: portal.url });
  } catch (err) {
    console.error("Stripe portal hiba:", err.message);
    res.status(500).json({ error: err.message });
  }
});

// ── Stripe: Előfizetés státusz ────────────────────────────────
app.get("/api/stripe/status", auth.requireLogin, (req, res) => {
  const user = usersDb.findById(req.user.id);
  res.json({
    plan:            user?.plan || "free",
    paidUntil:       user?.paidUntil || null,
    hasStripe:       !!user?.stripeCustomerId,
    stripeConfigured: !!stripe && !!STRIPE_PRICE,
  });
});


// ══════════════════════════════════════════════════════════════
// ── TELEGRAM BOT (kétirányú) ──────────────────────────────────
// ══════════════════════════════════════════════════════════════

const TG_BOT_API = `https://api.telegram.org/bot${TG_BOT_TOKEN}`;

async function tgSend(chatId, text, extra = {}) {
  if (!TG_BOT_TOKEN) return;
  await fetch(`${TG_BOT_API}/sendMessage`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML", ...extra })
  }).catch(e => console.error("tgSend hiba:", e.message));
}

async function tgTyping(chatId) {
  if (!TG_BOT_TOKEN) return;
  await fetch(`${TG_BOT_API}/sendChatAction`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, action: "typing" })
  }).catch(() => {});
}

// ── Meccs elemzés szerver oldalon (bot számára) ───────────────
async function analyzeForBot(query) {
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "anthropic-beta": "web-search-2025-03-05" },
    body: JSON.stringify({
      model: "claude-sonnet-4-6", max_tokens: 4000,
      tools: [{ type: "web_search_20250305", name: "web_search" }],
      messages: [{ role: "user", content:
        `Te egy profi labdarúgás-fogadási elemző vagy. Kizárólag helyes, igényes magyar nyelven írj. Kerüld a zsargont.

A kérés: "${query}"

Ha nem focimeccs, válaszolj: "NEM ÉRTEM: [magyarázat]"

Készíts rövid, tömör elemzést Telegram-ra optimalizálva (max 800 karakter). Struktúra:

⚽ <b>[Meccs neve]</b>

<b>Forma:</b> [1-2 mondat, számokkal]
<b>H2H:</b> [1 mondat]
<b>Sérülések:</b> [1 mondat]

<b>Top tippek:</b>
1. [Tipp] @ [odds] – [Megbízhatóság: MAGAS/KÖZEPES]
2. [Tipp] @ [odds] – [Megbízhatóság: MAGAS/KÖZEPES]

Ne használj csillagot (*) vagy hashtaget (#). Csak HTML bold (<b>) formázást.`
      }]
    })
  });
  const data = await r.json();
  const text = (data.content?.filter(b => b.type === "text").map(b => b.text) || []).join("\n").trim();
  return text || "Az elemzés sikertelen. Próbáld újra.";
}

// ── Bot parancsok kezelése ────────────────────────────────────
async function handleBotUpdate(update) {
  const msg    = update.message || update.edited_message;
  if (!msg?.text) return;
  if (msg.chat.type !== "private") return;  // csak privát chat, ne csatorna/csoport
  const chatId = msg.chat.id;
  const text   = msg.text.trim();
  const userId = msg.from?.id;

  // /start
  if (text === "/start" || text.startsWith("/start ")) {
    await tgSend(chatId,
      `⚽ <b>Üdv a 90perc.hu botban!</b>\n\n` +
      `<b>Parancsok:</b>\n` +
      `/tippek – Mai jóváhagyott tippek\n` +
      `/elemzes [meccs] – AI meccs elemzés (Pro)\n` +
      `/help – Súgó\n\n` +
      `<a href="https://90perc.hu">90perc.hu →</a>`
    );
    return;
  }

  // /help
  if (text === "/help") {
    await tgSend(chatId,
      `<b>90perc.hu Bot – Súgó</b>\n\n` +
      `/tippek – Megmutatja a mai jóváhagyott tippeket\n` +
      `/elemzes [meccs] – AI elemzés egy meccsre (Pro előfizetők)\n` +
      `  Példa: <code>/elemzes Bayern - Dortmund</code>\n\n` +
      `<a href="https://90perc.hu/elofizetes.html">Pro előfizetés</a>`
    );
    return;
  }

  // /tippek
  if (text === "/tippek") {
    const approved = history.filter(t =>
      isApproved(t) && t.result === "pending" && t.type !== "combo"
    );
    if (!approved.length) {
      await tgSend(chatId, "⚽ Ma még nincsenek jóváhagyott tippek. Nézz vissza később!");
      return;
    }
    const lines = approved.slice(0, 5).map(t =>
      `• <b>${t.match}</b>\n  ${t.market}: <b>${t.pick}</b> @ ${t.odds}`
    ).join("\n\n");
    await tgSend(chatId,
      `⚽ <b>Mai tippek (${approved.length} db)</b>\n\n${lines}\n\n` +
      `<a href="https://90perc.hu/tippek.html">Összes tipp →</a>`
    );
    return;
  }


  // /elemzes
  if (text.startsWith("/elemzes")) {
    const query = text.replace("/elemzes", "").trim();
    if (!query) {
      await tgSend(chatId, "❓ Add meg a meccs nevét!\nPélda: <code>/elemzes Bayern - Dortmund</code>");
      return;
    }
    // Admin bypass: ha ADMIN_TELEGRAM_CHAT_ID be van állítva és egyezik, azonnal engedélyezzük
    const ADMIN_TG_ID = process.env.ADMIN_TELEGRAM_CHAT_ID;
    if (ADMIN_TG_ID && String(chatId) === String(ADMIN_TG_ID)) {
      console.log(`Bot /elemzes – admin bypass (ADMIN_TELEGRAM_CHAT_ID), chatId: ${chatId}`);
      // Rate limit nincs adminra → egyből elemzünk
    } else {
      // Pro ellenőrzés normál felhasználóknak
      const allLinked = usersDb.all().filter(u => u.telegramChatId === String(chatId));
      console.log(`Bot /elemzes – chatId: ${chatId}, linked fiókok: ${allLinked.map(u => u.email + "/" + u.plan).join(", ") || "nincs"}`);
      const linked = allLinked.find(u => u.isAdmin || u.plan === "pro") || allLinked[0];
      const hasProAccess = linked && (linked.isAdmin || linked.plan === "pro" ||
        (process.env.ADMIN_EMAIL && linked.email === process.env.ADMIN_EMAIL));
      if (!hasProAccess) {
        await tgSend(chatId,
          `🔒 <b>Pro előfizetés szükséges</b>\n\n` +
          `Az AI meccs elemzés csak Pro előfizetőknek elérhető.\n` +
          `<a href="https://90perc.hu/elofizetes.html">Előfizetek – 14 990 Ft/hó →</a>`
        );
        return;
      }
      // Rate limit: max 5 elemzés/nap
      const isAdminUser = linked.isAdmin || (process.env.ADMIN_EMAIL && linked.email === process.env.ADMIN_EMAIL);
      if (!isAdminUser) {
        const today = todayHU();
        linked._tgDailyCount = linked._tgDailyCount || {};
        const count = linked._tgDailyCount[today] || 0;
        if (count >= 5) {
          await tgSend(chatId, "⏳ Napi elemzési limit elérve (5/nap). Holnap folytathatod.");
          return;
        }
        linked._tgDailyCount[today] = count + 1;
        usersDb.update(linked.id, { _tgDailyCount: linked._tgDailyCount });
      }
    }
    if (!hasProAccess) {
      await tgSend(chatId,
        `🔒 <b>Pro előfizetés szükséges</b>\n\n` +
        `Az AI meccs elemzés csak Pro előfizetőknek elérhető.\n` +
        `<a href="https://90perc.hu/elofizetes.html">Előfizetek – 14 990 Ft/hó →</a>`
      );
      return;
    }


    await tgSend(chatId, `🔍 Elemzem: <b>${query}</b>...\nEz 30-60 másodpercig tarthat.`);
    // Folyamatos "typing" jelzés amíg az AI dolgozik
    const typingInterval = setInterval(() => tgTyping(chatId), 4000);
    try {
      const result = await analyzeForBot(query);
      clearInterval(typingInterval);
      // Telegram max 4096 karakter
      const chunks = result.match(/.{1,4000}/gs) || [result];
      for (const chunk of chunks) await tgSend(chatId, chunk);
    } catch(e) {
      clearInterval(typingInterval);
      console.error("Bot elemzés hiba:", e.message);
      await tgSend(chatId, "❌ Elemzési hiba. Próbáld újra néhány perc múlva.");
    }
    return;
  }

  // Ismeretlen parancs
  if (text.startsWith("/")) {
    await tgSend(chatId, "❓ Ismeretlen parancs. Írd: /help");
  }
}

// ── Telegram bot webhook endpoint ─────────────────────────────
// Telegram GET-tel is ellenőrzi a webhookot
app.get("/api/telegram/bot", (req, res) => res.sendStatus(200));

app.post("/api/telegram/bot", express.json(), async (req, res) => {
  res.sendStatus(200); // Telegram-nak azonnal válaszolunk
  try { await handleBotUpdate(req.body); } catch(e) { console.error("Bot hiba:", e.message); }
});

// Telegram linking eltávolítva



// ── Globális hibakezelők – szerver ne omoljon össze ──────────────
process.on("uncaughtException", (err) => {
  console.error("UNCAUGHT EXCEPTION:", err.message);
  console.error(err.stack);
  // NE lépjünk ki – a szerver maradjon fent
});

process.on("unhandledRejection", (reason, promise) => {
  console.error("UNHANDLED REJECTION:", reason);
  // NE lépjünk ki – a szerver maradjon fent
});


// ── Admin: paidUntil szinkronizálás Stripe-ból ───────────────
app.post("/api/admin/sync-stripe", async (req, res) => {
  if (!requireAdmin(req, res)) return;
  if (!stripe) return res.status(500).json({ error: "Stripe nincs konfigurálva" });
  const proUsers = usersDb.all().filter(u => u.plan === "pro");
  console.log(`Stripe sync: ${proUsers.length} pro felhasználó keresése...`);
  let updated = 0;
  for (const u of proUsers) {
    try {
      let sub = null;
      // 1. Ha van stripeCustomerId, azzal keresünk
      if (u.stripeCustomerId) {
        const subs = await stripe.subscriptions.list({ customer: u.stripeCustomerId, status: "active", limit: 1 });
        if (subs.data.length) sub = subs.data[0];
      }
      // 2. Ha nincs vagy nem találtuk, email alapján keresünk
      if (!sub) {
        const customers = await stripe.customers.list({ email: u.email, limit: 1 });
        if (customers.data.length) {
          const cust = customers.data[0];
          const subs = await stripe.subscriptions.list({ customer: cust.id, status: "active", limit: 1 });
          if (subs.data.length) {
            sub = subs.data[0];
            usersDb.update(u.id, { stripeCustomerId: cust.id });
            console.log(`  Stripe customer ID beállítva: ${u.email} → ${cust.id}`);
          }
        }
      }
      if (sub) {
        const paidUntil = new Date(sub.current_period_end * 1000).toISOString();
        usersDb.update(u.id, { paidUntil, subscriptionStatus: "active", currentPeriodEnd: paidUntil });
        console.log(`  Stripe sync OK: ${u.email} → lejár: ${paidUntil}`);
        updated++;
      } else {
        console.log(`  Nincs aktív Stripe előfizetés: ${u.email}`);
      }
    } catch(e) { console.error(`Stripe sync hiba (${u.email}):`, e.message); }
  }
  res.json({ ok: true, updated, total: proUsers.length });
});


app.listen(PORT, () => {
  console.log(`90perc.hu fut: http://localhost:${PORT}`);
  if (TG_BOT_TOKEN) {
    console.log("✓ Telegram bot aktív – webhook: /api/telegram/bot");
  } else {
    console.log("⚠️  TG_BOT_TOKEN nincs beállítva – bot inaktív");
  }
});

if (!ADMIN_PWD) {
  console.warn("⚠️  FIGYELEM: ADMIN_PASSWORD nincs beállítva – az admin végpontok (törlés, eredményjelölés, stat-küldés, frissítés) VÉDTELENEK! Állítsd be a Render Environment Variables között.");
}
if (FOOTBALLDATA_TOKEN) {
  (async () => {
    try {
      const r = await fetch("https://api.football-data.org/v4/competitions", { headers: { "X-Auth-Token": FOOTBALLDATA_TOKEN } });
      if (!r.ok) {
        console.warn(`⚠️  football-data.org token NEM működik (HTTP ${r.status}). Ellenőrizd a FOOTBALLDATA_TOKEN értékét a Renderen.`);
        return;
      }
      const j = await r.json().catch(() => ({}));
      const comps = Array.isArray(j.competitions) ? j.competitions : [];
      const codes = comps.map(c => c.code);
      const hasWC = codes.includes("WC");
      console.log(`✓ football-data.org bekötve – ${comps.length} elérhető sorozat${hasWC ? " (VB benne van ✓)" : " (⚠️ a VB [WC] NINCS a csomagban)"}. A kiértékelés a 90 perces eredményt használja.`);
    } catch (e) {
      console.warn("⚠️  football-data.org ellenőrzés sikertelen:", e.message);
    }
  })();
} else {
  console.log("ℹ️  football-data.org token (FOOTBALLDATA_TOKEN) nincs beállítva – a kiértékelés az odds API végeredményét használja (kieséses/hosszabbításos meccseknél pontatlan lehet).");
}

const lastRun = loadLastRun();
if (lastRun) console.log(`Utolsó futás: ${Math.round((Date.now() - new Date(lastRun).getTime()) / 60000)} perce`);
// Meccs lista lekérése (mondomatutit Tipp Manager számára)
let lastMatchList = [];
(() => {
  try {
    const saved = JSON.parse(require("fs").readFileSync((process.env.DATA_DIR || "/data") + "/last_match_list.json", "utf8"));
    if (Array.isArray(saved)) lastMatchList = saved;
    console.log(`Meccs lista betöltve: ${lastMatchList.length} meccs`);
  } catch(e) {}
})();

app.get("/api/match-list", (req, res) => {
  if (!requireAdmin(req, res)) return;
  const tippedMatches = [...new Set(history
    .filter(t => t.result === "pending" && (t.type === "ai" || t.type === "combo" || t.type === "free"))
    .flatMap(t => t.type === "combo" ? (t.legs||[]).map(l => l.match) : [t.match])
    .filter(Boolean)
  )];
  const tippedPicks = history
    .filter(t => t.result === "pending" && t.type === "ai")
    .map(t => t.pick).filter(Boolean);
  res.json({ matches: lastMatchList, tippedMatches, tippedPicks, generatedAt: new Date().toISOString() });
});


