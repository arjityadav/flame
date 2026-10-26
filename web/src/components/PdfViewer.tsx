import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useEffect, useRef, useState } from "react";
import type { Box } from "../api";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const docs = new Map<string, Promise<pdfjs.PDFDocumentProxy>>();
function load(url: string) {
  if (!docs.has(url)) docs.set(url, pdfjs.getDocument({ url }).promise);
  return docs.get(url)!;
}

type Props = { url: string; pages: number[]; boxes: Box[] };

/** Renders the given pages and draws the paragraph's line boxes over them (PDF points, top-left origin). */
export function PdfViewer({ url, pages, boxes }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const el = host.current!;
    el.innerHTML = "";
    setError(null);
    (async () => {
      try {
        const doc = await load(url);
        const width = Math.min(el.parentElement!.clientWidth - 32, 900);
        let firstBox: HTMLElement | null = null;
        for (const pageNo of pages) {
          if (cancelled) return;
          const page = await doc.getPage(pageNo);
          const base = page.getViewport({ scale: 1 });
          const scale = width / base.width;
          const viewport = page.getViewport({ scale });
          const ratio = window.devicePixelRatio || 1;
          const wrap = document.createElement("div");
          wrap.className = "pdf-page";
          wrap.style.width = `${viewport.width}px`;
          wrap.style.height = `${viewport.height}px`;
          wrap.setAttribute("aria-label", `Page ${pageNo}`);
          const canvas = document.createElement("canvas");
          canvas.width = Math.floor(viewport.width * ratio);
          canvas.height = Math.floor(viewport.height * ratio);
          canvas.style.width = `${viewport.width}px`;
          canvas.style.height = `${viewport.height}px`;
          wrap.appendChild(canvas);
          el.appendChild(wrap);
          await page.render({ canvasContext: canvas.getContext("2d")!, viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined }).promise;
          for (const b of boxes.filter((b) => b.page === pageNo)) {
            const [x0, y0, x1, y1] = b.bbox;
            const mark = document.createElement("div");
            mark.className = "pdf-box";
            mark.style.left = `${x0 * scale - 4}px`;
            mark.style.top = `${y0 * scale - 1}px`;
            mark.style.width = `${(x1 - x0) * scale + 8}px`;
            mark.style.height = `${(y1 - y0) * scale + 2}px`;
            wrap.appendChild(mark);
            firstBox ??= mark;
          }
        }
        if (firstBox && !cancelled) {
          const scroller = el.parentElement!;
          const top = firstBox.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
          scroller.scrollTo({ top: Math.max(0, top - scroller.clientHeight / 3), behavior: "smooth" });
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "The PDF could not be shown.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url, pages.join(","), boxes]);

  return (
    <>
      {error && <div className="notice warn">The PDF could not be shown: {error}</div>}
      <div ref={host} style={{ display: "grid", gap: 12, justifyItems: "center" }} />
    </>
  );
}
