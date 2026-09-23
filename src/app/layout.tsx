import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: 'Poly METARs — auditable METAR observations',
  description:
    'Compare government METAR reports, inspect original evidence, and reproduce a transparent temperature selection policy.',
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
