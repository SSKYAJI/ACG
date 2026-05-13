import { useCallback, useEffect, useState } from "react";

type Props = {
  command: string;
};

export function CommandBlock({ command }: Props) {
  const [label, setLabel] = useState("Copy");

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(command);
      setLabel("Copied");
      return;
    } catch {
      setLabel("Copy failed");
      return;
    }
  }, [command]);

  useEffect(() => {
    if (label !== "Copied" && label !== "Copy failed") return;
    const t = window.setTimeout(() => setLabel("Copy"), 1600);
    return () => window.clearTimeout(t);
  }, [label]);

  const liveText = label === "Copied" ? "Copied command to clipboard" : undefined;

  return (
    <div className="cmd-inline card" aria-live={liveText ? "polite" : undefined}>
      <pre className="code-block cmd-pre" aria-label="Install command">
        {command}
      </pre>
      <button
        type="button"
        className="cmd-copy"
        aria-label={`Copy command: ${command}`}
        aria-pressed={label === "Copied"}
        data-state={label}
        onClick={onCopy}
      >
        {label}
      </button>
    </div>
  );
}
