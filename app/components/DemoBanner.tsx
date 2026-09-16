interface DemoBannerProps {
  url?: string;
}

const DEFAULT_URL = "https://github.com/aaweaver-actuary/tempo#running-locally";

export default function DemoBanner({ url = DEFAULT_URL }: DemoBannerProps) {
  return (
    <div className="demo-banner">
      Tempo practice demo · <a href={url}>Run full local Tempo</a>
    </div>
  );
}
