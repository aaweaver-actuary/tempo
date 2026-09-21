import type { Metadata } from 'next';
import '@lichess-org/chessground/assets/chessground.base.css';
import '@lichess-org/chessground/assets/chessground.brown.css';
import '@lichess-org/chessground/assets/chessground.cburnett.css';
import './globals.css';
import './responsive.css';
import RuntimeErrorGuard from './components/runtime-error-guard';

export const metadata: Metadata = {
  metadataBase: new URL('https://tempo-chess-opening-trainer.andyandyandyandy.chatgpt.site'),
  title: 'Tempo — Chess opening practice',
  description: 'A focused, fully local daily practice queue for your chess opening repertoire.',
  openGraph: {
    title: 'Tempo — Chess opening practice',
    description: 'Chess opening practice, one day at a time.',
    type: 'website',
    url: 'https://tempo-chess-opening-trainer.andyandyandyandy.chatgpt.site',
    images: [{ url: '/og.png', width: 1731, height: 909, alt: 'Tempo — Chess opening practice, one day at a time.' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Tempo — Chess opening practice',
    description: 'Chess opening practice, one day at a time.',
    images: ['/og.png'],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body><RuntimeErrorGuard>{children}</RuntimeErrorGuard></body>
    </html>
  );
}
