import "./activity-indicator.css";

interface ActivityIndicatorProps {
  label: string;
  detail?: string;
  variant?: "inline" | "block";
  tone?: "evidence" | "brass" | "muted";
  className?: string;
}

export function ActivityIndicator({
  label,
  detail,
  variant = "inline",
  tone = "evidence",
  className,
}: ActivityIndicatorProps) {
  const classes = [
    "activity-indicator",
    `activity-indicator--${variant}`,
    `activity-indicator--${tone}`,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      className={classes}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      aria-busy="true"
    >
      <span className="activity-indicator__orbit" aria-hidden="true" />
      <span className="activity-indicator__copy">
        <span>{label}</span>
        {detail ? <small>{detail}</small> : null}
      </span>
    </span>
  );
}
