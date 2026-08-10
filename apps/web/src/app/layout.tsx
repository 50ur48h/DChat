import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "data-agent",
  description: "AI-native data analysis platform",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
