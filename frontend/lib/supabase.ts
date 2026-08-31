"use client";

/**
 * lib/supabase.ts — Supabase browser client singleton.
 *
 * Uses the public anon key for client-side Realtime subscriptions.
 * Never uses the service role key in the browser.
 *
 * NEXT_PUBLIC_* values must be read at module scope so Next.js inlines them
 * into the client bundle at build time. Reading them inside a function can
 * leave them undefined in production and crash the page on first run.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const SUPABASE_URL = (process.env.NEXT_PUBLIC_SUPABASE_URL ?? "").trim();
const SUPABASE_ANON_KEY = (process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "").trim();

let _client: SupabaseClient | null = null;

export function isSupabaseConfigured(): boolean {
  return SUPABASE_URL.length > 0 && SUPABASE_ANON_KEY.length > 0;
}

export function getSupabaseClient(): SupabaseClient | null {
  if (_client) return _client;
  if (!isSupabaseConfigured()) return null;

  try {
    _client = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      realtime: {
        params: {
          eventsPerSecond: 10,
        },
      },
    });
  } catch {
    return null;
  }

  return _client;
}
