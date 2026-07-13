import { cn } from "../lib/utils";

const assetPath = (path: string) =>
  `${import.meta.env.BASE_URL}${path.replace(/^\/+/, "")}`;

// Atlas badge shared with the desktop app; asset lives in this app's public/.
export function BrandMark({
  className,
  ...props
}: React.ComponentProps<"span">) {
  return (
    <span
      className={cn(
        "inline-flex size-14 shrink-0 items-center justify-center",
        className,
      )}
      {...props}
    >
      <img
        alt=""
        className="size-full object-contain"
        src={assetPath("atlas-icon.png")}
      />
    </span>
  );
}
