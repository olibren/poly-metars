type SourceChecks = {
  source: string;
  airport_last_success_at?: Record<string, string | null>;
};

export function awcStale(
  collection: SourceChecks[] | undefined,
  icao: string,
  now: number,
): boolean {
  const last = collection?.find((source) => source.source === 'noaa_awc')
    ?.airport_last_success_at?.[icao];
  // Unknown checks (including older index formats) cannot establish a stale age.
  return !!last && now - Date.parse(last) >= 15 * 60 * 1000;
}
