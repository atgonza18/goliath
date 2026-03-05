import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Parse a date string safely, treating date-only strings as local time.
 *
 * JavaScript's `new Date("2026-03-05")` parses date-only ISO strings as UTC
 * midnight, which shifts the displayed date backward in US timezones (e.g.,
 * CST shows March 4 instead of March 5). This helper detects date-only
 * strings and appends "T12:00:00" (noon) to force local-time interpretation,
 * avoiding both the UTC rollback and any DST edge cases at midnight.
 *
 * Strings that already include a time component are parsed normally.
 */
export function parseLocalDate(dateStr: string): Date {
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
    return new Date(dateStr + 'T12:00:00');
  }
  return new Date(dateStr);
}
