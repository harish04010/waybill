import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Waybill: freight agents at work",
  description: "A dispatch board for AI agents that quote, book, and track vehicle shipments in parallel.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600&display=swap"
        />
      </head>
      <body>
        <nav className="hr-bar" aria-label="Portfolio">
          <a href="https://harish04010-portfolio.vercel.app">&larr; Harish&apos;s portfolio</a>
          <a href="https://github.com/harish04010/waybill">View the code on GitHub &rarr;</a>
        </nav>
        {children}
      </body>
    </html>
  );
}
