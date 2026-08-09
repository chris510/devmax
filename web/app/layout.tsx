import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, Newsreader } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const plexSans = IBM_Plex_Sans({
  variable: "--font-plex-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});
const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});
const newsreader = Newsreader({
  variable: "--font-newsreader",
  subsets: ["latin"],
  weight: ["400", "500"],
  style: ["normal", "italic"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const baseUrl = new URL(`${protocol}://${host}`);
  const imageUrl = new URL("/og.png", baseUrl).toString();

  return {
    metadataBase: baseUrl,
    title: "Unprompted — Remember the hard parts",
    description: "A private voice-first coach for honest recall and spaced retrieval practice.",
    icons: { icon: "/unprompted-icon.png", shortcut: "/unprompted-icon.png" },
    openGraph: {
      title: "Unprompted — Remember the hard parts",
      description: "One honest question at a time. Voice-first retrieval practice in two minutes.",
      type: "website",
      images: [{ url: imageUrl, width: 1635, height: 962, alt: "Unprompted — Remember the hard parts" }],
    },
    twitter: {
      card: "summary_large_image",
      title: "Unprompted — Remember the hard parts",
      description: "Two honest minutes at a time.",
      images: [imageUrl],
    },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${plexSans.variable} ${plexMono.variable} ${newsreader.variable}`}>
        {children}
      </body>
    </html>
  );
}
