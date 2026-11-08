// 戰情頁純函式。不抓行情、不改 DOM 以外的狀態。
function displayChange(change, changePct) {
  if (change === null || change === undefined || changePct === null || changePct === undefined) {
    return { text: "漲跌未知", kind: "unknown" };
  }
  const ch = Number(change);
  const chPct = Number(changePct);
  if (!Number.isFinite(ch) || !Number.isFinite(chPct)) {
    return { text: "漲跌未知", kind: "unknown" };
  }
  if (ch > 0) return { text: `▲ +${ch.toFixed(2)} (+${chPct.toFixed(2)}%)`, kind: "up" };
  if (ch < 0) return { text: `▼ ${ch.toFixed(2)} (${chPct.toFixed(2)}%)`, kind: "down" };
  return { text: "0.00 (0.00%)", kind: "flat" };
}

function quoteHeadline(inferred) {
  if (!inferred || inferred.current !== true || inferred.status !== "ok") {
    return "報價推斷：未知";
  }
  return `報價推斷：${inferred.label_display || inferred.label || "未知"}`;
}

function formatMarketStatus(market) {
  const aligned = market && market.source_aligned === true ? "來源日期已對齊" : "來源日期未對齊或未知";
  let when = "是否當期未知";
  if (market && market.current === true) when = "當期";
  else if (market && market.stale === true) when = "過期，不可當成當前情境";
  return `${aligned}｜${when}`;
}

function describeTreemapSummary(summary) {
  if (!summary) return { asOf: "資料日未知", change: "—", badge: "行情尚未核對" };
  const missing = summary.index_change_pct === null || summary.index_change_pct === undefined;
  let badge = "行情尚未核對";
  if (summary.status === "placeholder") badge = "內建占位，不是即時行情";
  else if (summary.verified === true) badge = "有交易所日期，不代表此頁正在監測";
  else if (summary.status === "unknown") badge = "交易日未知";
  return {
    asOf: summary.as_of_display || summary.as_of_date || "資料日未知",
    change: missing ? "—" : `${summary.index_change_pct >= 0 ? "+" : ""}${summary.index_change_pct}%`,
    badge: badge,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { displayChange, quoteHeadline, formatMarketStatus, describeTreemapSummary };
}
