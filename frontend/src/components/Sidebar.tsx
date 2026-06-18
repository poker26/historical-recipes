"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: "\u25A3" },
  { href: "/books", label: "Books", icon: "\u25A1" },
  { href: "/recipes", label: "Recipes", icon: "\u2606" },
  { href: "/plants", label: "Herbarium", icon: "\u2698" },
  { href: "/compounds", label: "Compounds", icon: "\u269B" },
  { href: "/oils", label: "Essential Oils", icon: "\u2697" },
  { href: "/search", label: "Search", icon: "\u2315" },
  { href: "/quality", label: "\u041a\u0430\u0447\u0435\u0441\u0442\u0432\u043e \u0434\u0430\u043d\u043d\u044b\u0445", icon: "\u2713" },
  { href: "/moderation", label: "\u041c\u043e\u0434\u0435\u0440\u0430\u0446\u0438\u044f", icon: "\u26d4" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">Historical Recipes</div>
      <nav>
        {NAV_ITEMS.map((item) => {
          const isActive =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={isActive ? "active" : ""}
            >
              <span>{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
