"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: "\u25A3" },
  { href: "/books", label: "Books", icon: "\u25A1" },
  { href: "/recipes", label: "Recipes", icon: "\u2606" },
  { href: "/plants", label: "Herbarium", icon: "\u2698" },
  { href: "/dictionaries", label: "Dictionaries", icon: "\u2261" },
  { href: "/search", label: "Search", icon: "\u2315" },
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
