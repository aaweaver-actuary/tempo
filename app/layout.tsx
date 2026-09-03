import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Tempo — Chess opening practice',
  description: 'A focused, fully local daily practice queue for your chess opening repertoire.',
  openGraph: {
    title: 'Tempo — Chess opening practice',
    description: 'Chess opening practice, one day at a time.',
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Tempo — Chess opening practice',
    description: 'Chess opening practice, one day at a time.',
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
