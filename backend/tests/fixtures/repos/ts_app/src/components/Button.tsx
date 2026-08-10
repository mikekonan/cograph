import { useState } from "react";

type Props = { label: string };

export const Button = ({ label }: Props) => {
  const [busy, setBusy] = useState(false);
  return (
    <button disabled={busy} onClick={() => setBusy(true)}>
      {label}
    </button>
  );
};
