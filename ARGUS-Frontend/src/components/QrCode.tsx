import QRCode from "qrcode";
import { useEffect, useState } from "react";

export default function QrCode({ value, size = 200 }: { value: string; size?: number }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let cancelled = false;
    QRCode.toDataURL(value, { width: size, margin: 1, errorCorrectionLevel: "M" }).then((url) => { if (!cancelled) setSrc(url); }).catch(() => setSrc(""));
    return () => { cancelled = true; };
  }, [value, size]);
  return src
    ? <img className="qr" src={src} width={size} height={size} alt="QR code for your authenticator app" />
    : <div className="qr" style={{ width: size, height: size }} aria-hidden="true" />;
}
