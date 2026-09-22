export function formatNumber(value: number | null, decimals = 3) {
  if (value === null || value === undefined) return "—";
  return value.toFixed(decimals);
}

export function formatSigned(value: number | null, decimals = 3) {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "±";
  return `${sign}${Math.abs(value).toFixed(decimals)}`;
}
