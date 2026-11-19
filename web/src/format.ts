// Mise en forme française des montants, nombres et dates.

const MICRO = 1_000_000;

const credits = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const integer = new Intl.NumberFormat("fr-FR");
const compact = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });
const dateTime = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** Micro-crédits → « 12,40 ». Le serveur compte en entiers, l'affichage arrondit au centième. */
export function formatCredits(micro: number): string {
  return credits.format(micro / MICRO);
}

/** Montant signé : « + 10,00 » ou « − 1,86 » (vrai signe moins). */
export function formatSignedCredits(micro: number): string {
  const sign = micro < 0 ? "−" : "+";
  return `${sign} ${credits.format(Math.abs(micro) / MICRO)}`;
}

export function formatInteger(value: number): string {
  return integer.format(value);
}

export function formatCompact(value: number): string {
  return value < 10_000 ? integer.format(value) : compact.format(value);
}

export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso));
}

/** Date AAAA-MM-JJ en UTC, comme les jours renvoyés par /api/usage. */
export function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}
