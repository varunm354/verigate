// Minimal, dependency-free CSV parser sufficient for VeriGate's committed
// research CSVs (research/results/<campaign_id>/*.csv): no embedded
// newlines, but fields may contain quoted commas (e.g. exclusion_reason).
// Not a general-purpose CSV library -- kept intentionally small so the
// frontend's data pipeline has zero extra npm dependencies.

/**
 * @param {string} text Raw CSV file contents.
 * @returns {Record<string, string>[]} One object per data row, keyed by header.
 */
export function parseCsv(text) {
  const lines = text.replace(/\r\n/g, "\n").split("\n").filter((l) => l.length > 0);
  if (lines.length === 0) return [];
  const header = parseLine(lines[0]);
  return lines.slice(1).map((line) => {
    const values = parseLine(line);
    /** @type {Record<string, string>} */
    const row = {};
    header.forEach((key, i) => {
      row[key] = values[i] ?? "";
    });
    return row;
  });
}

/** @param {string} line */
function parseLine(line) {
  const values = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (inQuotes) {
      if (char === '"') {
        if (line[i + 1] === '"') {
          current += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        current += char;
      }
    } else if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  values.push(current);
  return values;
}
