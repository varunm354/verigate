import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://verigate.vercel.app";
const TITLE = "VeriGate — Does a stated visible-test pass bias AI reviewer confidence?";
const DESCRIPTION =
  "An exploratory controlled study of AI code-review calibration: 12 candidates, 108 reviewer observations, two Python tasks. Sanitized results, deterministic figures, and full reproduction instructions.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: TITLE,
  description: DESCRIPTION,
  applicationName: "VeriGate",
  authors: [{ name: "Varun Mohanraj" }],
  keywords: [
    "AI code review",
    "LLM evaluation",
    "reviewer calibration",
    "coding agents",
    "exploratory research",
  ],
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    url: "/",
    siteName: "VeriGate",
    title: TITLE,
    description: DESCRIPTION,
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="tone-ink flex min-h-full flex-col">{children}</body>
    </html>
  );
}
