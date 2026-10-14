"use client";

// Ссылка «в приложение», которая ведёт в магазин устройства: iPhone и iPad в App Store,
// Android в Google Play, компьютер в RuStore. Устройство определяется в браузере: страница
// карточки кэшируется целиком, и на сервере выбирать магазин по заголовкам нельзя.
import { useEffect, useState, type ReactNode } from "react";
import { APPSTORE_URL, GOOGLE_PLAY_URL, RUSTORE_URL } from "../app/lib";

function storeFor(ua: string): string {
  if (/iPhone|iPad|iPod/i.test(ua) || (/Macintosh/i.test(ua) && typeof navigator !== "undefined" && navigator.maxTouchPoints > 1)) {
    return APPSTORE_URL || RUSTORE_URL;
  }
  if (/Android/i.test(ua)) return GOOGLE_PLAY_URL;
  return RUSTORE_URL;
}

export function AppStoreLink({ className, children }: { className?: string; children: ReactNode }) {
  const [href, setHref] = useState(RUSTORE_URL);
  useEffect(() => { setHref(storeFor(navigator.userAgent)); }, []);
  return (
    <a href={href} target="_blank" rel="noopener" className={className}>
      {children}
    </a>
  );
}
