"use client";

// A link that fires a named Metrika goal on click (used for the qtickets buy
// links so the funnel «site → покупка» is measurable). Server components can't
// attach handlers, hence this tiny client wrapper.
import { ymGoal } from "./lib";

export default function TrackedLink({
  href, goal, className, children,
}: {
  href: string;
  goal: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={className}
      onClick={() => ymGoal(goal)}
    >
      {children}
    </a>
  );
}
