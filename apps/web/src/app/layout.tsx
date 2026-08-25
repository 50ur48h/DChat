import type { Metadata } from "next";

import { SessionProvider } from "@/lib/auth/session";
import { THEME_STORAGE_KEY } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: "data-agent",
  description: "AI-native data analysis platform",
};

/**
 * Set the theme before the first paint (D-046).
 *
 * **This is the one place the app injects a script, and it is worth the
 * exception.** React cannot help here by construction: the attribute has to be
 * on `<html>` before the browser paints anything, and the earliest React can run
 * is after hydration — several hundred milliseconds during which someone who
 * chose dark is looking at a white screen. Every theme toggle on the web solves
 * it this way.
 *
 * It is safe to read as `dangerouslySetInnerHTML` because nothing in it is
 * dynamic: the only interpolation is `THEME_STORAGE_KEY`, a compile-time
 * constant from our own module, and the script writes an attribute rather than
 * any markup. It reads storage, sets one attribute, and stops.
 *
 * Wrapped in try/catch because a browser with site data blocked *throws* on
 * `localStorage` rather than returning null, and an exception here would run
 * before anything else on the page.
 */
const THEME_SCRIPT = `try{var t=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});if(t==="dark"){document.documentElement.setAttribute("data-theme","dark")}}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // `suppressHydrationWarning` because the script above legitimately changes
    // this element between the server's HTML and hydration. It is scoped to
    // `<html>` itself and does not extend to anything rendered inside it.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
