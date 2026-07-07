"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, GREETING, SUGGESTS, ymGoal } from "./lib";

type Msg = { role: "user" | "assistant"; content: string };

function deviceId(): string {
  try {
    let id = localStorage.getItem("nastoiki_device");
    if (!id) {
      id = (crypto.randomUUID?.() || String(Math.random()).slice(2));
      localStorage.setItem("nastoiki_device", id);
    }
    return id;
  } catch {
    return "anon";
  }
}

// Lightweight Markdown renderer (no deps): the agent replies in Markdown, so
// **bold**, *italic*, `code`, > quotes, - lists and #headings render as real
// formatting instead of literal asterisks. Streamed partial markdown degrades
// gracefully (an unclosed **… stays literal until its pair arrives).
function renderInline(text: string, kp: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const re = /\*\*([^*]+)\*\*|\*([^*\n]+)\*|_([^_\n]+)_|`([^`]+)`|(https?:\/\/[^\s)]+)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1] !== undefined) nodes.push(<strong key={`${kp}-${i}`}>{m[1]}</strong>);
    else if (m[2] !== undefined) nodes.push(<em key={`${kp}-${i}`}>{m[2]}</em>);
    else if (m[3] !== undefined) nodes.push(<em key={`${kp}-${i}`}>{m[3]}</em>);
    else if (m[4] !== undefined) nodes.push(<code key={`${kp}-${i}`}>{m[4]}</code>);
    else if (m[5] !== undefined)
      nodes.push(
        <a key={`${kp}-${i}`} href={m[5]} target="_blank" rel="noopener noreferrer">
          {m[5].replace(/^https?:\/\//, "")}
        </a>
      );
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function renderMarkdown(text: string): React.ReactNode[] {
  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      out.push(<div key={k} className="md-h">{renderInline(h[2], `h${k}`)}</div>);
      k++; i++; continue;
    }
    if (/^>\s?/.test(line)) {
      const q: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) { q.push(lines[i].replace(/^>\s?/, "")); i++; }
      out.push(<blockquote key={k}>{renderInline(q.join("\n"), `q${k}`)}</blockquote>);
      k++; continue;
    }
    if (/^\s*[-*•]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*•]\s+/, "")); i++; }
      out.push(
        <ul key={k}>{items.map((it, j) => <li key={j}>{renderInline(it, `l${k}-${j}`)}</li>)}</ul>
      );
      k++; continue;
    }
    if (line.trim() === "") { i++; continue; }
    const para: string[] = [];
    while (
      i < lines.length && lines[i].trim() !== "" &&
      !/^>\s?/.test(lines[i]) && !/^\s*[-*•]\s+/.test(lines[i]) && !/^#{1,4}\s+/.test(lines[i])
    ) { para.push(lines[i]); i++; }
    out.push(<p key={k}>{renderInline(para.join("\n"), `p${k}`)}</p>);
    k++;
  }
  return out;
}

export default function Chat() {
  const [messages, setMessages] = useState<Msg[]>([{ role: "assistant", content: GREETING }]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [tool, setTool] = useState<string | null>(null);
  const [location, setLocation] = useState<string | null>(null);
  const [geoBusy, setGeoBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [messages, tool]);

  async function requestGeo() {
    if (!navigator.geolocation) return;
    setGeoBusy(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation(`${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)}`);
        setGeoBusy(false);
      },
      () => setGeoBusy(false),
      { timeout: 8000 }
    );
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    ymGoal("agent_message");
    setInput("");
    // Instant feedback: show the spinner the moment the question is sent, before
    // any server round-trip (the topic-gate + first tool call are non-streamed, so
    // the first SSE event can be several seconds out — without this it feels hung).
    setTool("Думаю над вашим вопросом");

    const history: Msg[] = [...messages, { role: "user", content: q }];
    // Append an empty assistant bubble we stream into.
    setMessages([...history, { role: "assistant", content: "" }]);
    setBusy(true);

    let acc = "";
    const bump = () =>
      setMessages((prev) => {
        const copy = prev.slice();
        copy[copy.length - 1] = { role: "assistant", content: acc };
        return copy;
      });

    try {
      const resp = await fetch(`${API_BASE}/api/agent/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Device-Id": deviceId() },
        body: JSON.stringify({ messages: history, location }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          if (!raw.startsWith("data:")) continue;
          let ev: any;
          try {
            ev = JSON.parse(raw.slice(5).trim());
          } catch {
            continue;
          }
          if (ev.type === "delta") {
            setTool(null);
            acc += ev.text;
            bump();
          } else if (ev.type === "tool") {
            setTool("Листаю старинные книги");
          } else if (ev.type === "error") {
            acc += (acc ? "\n\n" : "") + ev.text;
            bump();
          }
        }
      }
    } catch {
      acc += (acc ? "\n\n" : "") + "Связь с лабораторией прервалась. Попробуйте ещё раз 😊";
      bump();
    } finally {
      setBusy(false);
      setTool(null);
    }
  }

  return (
    <div className="chat">
      <div className="chat-head">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img className="sigil" src="/img/alchemist.jpg" alt="Мастер настоек" />
        <div>
          <b>Мастер настоек</b>
          <span className="sub">по дореволюционным книгам</span>
        </div>
        <span className="dot" />
      </div>

      <div className="chat-log" ref={logRef}>
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.role === "assistant" ? renderMarkdown(m.content) : m.content}
            {busy && i === messages.length - 1 && m.role === "assistant" && !m.content ? "…" : null}
          </div>
        ))}
        {tool ? (
          <div className="tool-note">
            <span className="spin" /> {tool}…
          </div>
        ) : null}
      </div>

      {messages.length <= 1 ? (
        <div className="suggests">
          {SUGGESTS.map((s) => (
            <button key={s} className="chip" onClick={() => send(s)} disabled={busy}>
              {s}
            </button>
          ))}
        </div>
      ) : null}

      <div className="chat-input">
        <button
          className={`geo ${location ? "on" : ""}`}
          title={location ? `Рядом: ${location}` : "Поделиться геолокацией для советов «что растёт рядом»"}
          onClick={requestGeo}
          disabled={geoBusy}
        >
          📍
        </button>
        <textarea
          rows={1}
          placeholder="Спросите мастера…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
          disabled={busy}
        />
        <button className="send" onClick={() => send(input)} disabled={busy || !input.trim()}>
          ➤
        </button>
      </div>
    </div>
  );
}
