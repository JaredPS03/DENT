import type { Metadata, Viewport } from "next";
import "./globals.css";

// No patient data ever goes in titles, URLs or localStorage (.claude/rules/panel.md).
export const metadata: Metadata = {
  title: "Axon Dental",
  description: "Panel del consultorio: agenda, conversaciones y configuración.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0B1736",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es-MX" className="h-full antialiased">
      <body className="flex min-h-full flex-col bg-white text-axon-night">{children}</body>
    </html>
  );
}
