type Station = { icao: string; timezone: string };

export function resolutionUrl(icao: string, date: string): string {
  return `/?${new URLSearchParams({ airport: icao.toUpperCase(), date })}`;
}

export function resolveSelection(
  search: string,
  airports: Station[],
  now = new Date(),
): { icao: string; date: string } {
  const params = new URLSearchParams(search);
  if (params.getAll('airport').length > 1 || params.getAll('date').length > 1)
    throw new Error('Use one airport and one local date in the page URL.');
  const icao = (params.get('airport') ?? 'ZSQD').toUpperCase();
  const airport = airports.find((item) => item.icao === icao);
  if (!airport) throw new Error(`Unknown airport: ${icao || '(empty)'}.`);
  const date = params.get('date') ?? new Intl.DateTimeFormat('en-CA', {
    timeZone: airport.timezone,
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(now);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) ||
      !Number.isFinite(Date.parse(`${date}T00:00:00Z`)) ||
      new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date)
    throw new Error('Invalid local date. Use YYYY-MM-DD.');
  return { icao, date };
}
