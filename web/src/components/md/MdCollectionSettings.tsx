import type { MdCollection, MdCollectionVisibility } from "@/api/mdCollections";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { useAuth } from "@/hooks/useAuth";
import { useUpdateMdCollection } from "@/hooks/useMdCollections";
import { hasAdminAccess } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { Eye } from "lucide-react";
import type { ReactNode } from "react";
import { MdCollectionVisibilityBadge } from "./MdCollectionVisibilityBadge";

type MdCollectionSettingsProps = {
  collection: MdCollection;
  className?: string;
};

export function MdCollectionSettings({ collection, className }: MdCollectionSettingsProps) {
  const { user } = useAuth();
  const updateCollection = useUpdateMdCollection(collection.id);
  const canManage = hasAdminAccess(user?.role) || user?.id === collection.owner_id;

  return (
    <section
      aria-label="Collection settings"
      className={cn(
        "flex flex-col gap-3.5 rounded-[var(--radius-md)] border p-4",
        "border-[color:var(--color-border-subtle)]",
        "bg-[color:var(--color-bg-surface)]",
        className,
      )}
    >
      {canManage ? (
        <SettingBlock
          title="Visibility"
          icon={Eye}
          trailing={
            <MdCollectionVisibilityBadge visibility={collection.visibility} className="shrink-0" />
          }
        >
          <Select
            value={collection.visibility}
            onValueChange={(value) => {
              updateCollection.mutate({
                visibility: value as MdCollectionVisibility,
              });
            }}
            disabled={updateCollection.isPending}
          >
            <SelectTrigger className="w-36 flex-shrink-0">
              <span className="truncate text-sm capitalize">
                {collection.visibility === "admin_only" ? "Admin-only" : collection.visibility}
              </span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="private">Private</SelectItem>
              <SelectItem value="public">Public</SelectItem>
              <SelectItem value="admin_only">Admin-only</SelectItem>
            </SelectContent>
          </Select>
        </SettingBlock>
      ) : (
        <div>
          <h3 className="flex items-center gap-2 text-sm font-medium text-[color:var(--color-fg)]">
            <Eye className="h-3.5 w-3.5 text-[color:var(--color-fg-muted)]" aria-hidden />
            Visibility
          </h3>
          <div className="mt-2">
            <MdCollectionVisibilityBadge visibility={collection.visibility} />
          </div>
        </div>
      )}

      {updateCollection.isError && (
        <p role="alert" className="text-xs text-[color:var(--color-danger)]">
          Couldn&apos;t update collection settings. Try again.
        </p>
      )}
    </section>
  );
}

function SettingBlock({
  title,
  icon: Icon,
  trailing,
  children,
}: {
  title: string;
  icon: React.ComponentType<React.SVGProps<SVGSVGElement>>;
  trailing?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-medium text-[color:var(--color-fg)]">
          <Icon className="h-3.5 w-3.5 text-[color:var(--color-fg-muted)]" aria-hidden />
          {title}
        </h3>
        {trailing}
      </div>
      {children}
    </div>
  );
}
