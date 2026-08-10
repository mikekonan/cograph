import type { MdCollection } from "@/api/mdCollections";
import { cn } from "@/lib/utils";
import { MdCollectionCard } from "./MdCollectionCard";

type MdCollectionGridProps = {
  collections: MdCollection[];
  className?: string;
};

/** Responsive grid: 1 → 2 → 3 columns. */
export function MdCollectionGrid({ collections, className }: MdCollectionGridProps) {
  return (
    <div className={cn("grid gap-4 grid-cols-1 md:grid-cols-2 lg:grid-cols-3", className)}>
      {collections.map((collection) => (
        <MdCollectionCard key={collection.id} collection={collection} />
      ))}
    </div>
  );
}
