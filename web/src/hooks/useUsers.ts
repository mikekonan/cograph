import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type AdminUser,
  type CreateUserPayload,
  createAdminUser,
  deleteAdminUser,
  listAdminUsers,
  type UpdateUserPayload,
  updateAdminUser,
} from "@/api/users";

const USERS_KEY = ["admin", "users"] as const;

export function useAdminUsers() {
  return useQuery<AdminUser[]>({
    queryKey: USERS_KEY,
    queryFn: listAdminUsers,
  });
}

export function useCreateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateUserPayload) => createAdminUser(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: USERS_KEY });
    },
  });
}

export function useUpdateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, payload }: { userId: string; payload: UpdateUserPayload }) =>
      updateAdminUser(userId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: USERS_KEY });
    },
  });
}

export function useDeleteAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => deleteAdminUser(userId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: USERS_KEY });
    },
  });
}
