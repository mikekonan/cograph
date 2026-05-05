export type NativeDocsSurfaceMode = "none" | "secondary" | "primary";

export const PRIMARY_NATIVE_DOCS_MIN = 4;

export function getNativeDocsSurfaceMode(
  documentsCount: number | null | undefined,
): NativeDocsSurfaceMode {
  const count = documentsCount ?? 0;
  if (count <= 0) return "none";
  if (count < PRIMARY_NATIVE_DOCS_MIN) return "secondary";
  return "primary";
}

export function getNativeDocsActionLabel({
  documentsCount,
  hasReadme,
}: {
  documentsCount: number | null | undefined;
  hasReadme: boolean;
}): string | null {
  const mode = getNativeDocsSurfaceMode(documentsCount);
  if (mode === "none") return null;
  if ((documentsCount ?? 0) === 1) {
    return hasReadme ? "Open README" : "Open native doc";
  }
  if (mode === "secondary") return "Open native docs";
  return "Open Docs";
}
