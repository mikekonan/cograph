import {
  addGroupMembers,
  createAdminGroup,
  deleteAdminGroup,
  deleteGroupCollectionGrant,
  deleteGroupRepositoryGrant,
  listAdminGroups,
  listGroupCollectionGrants,
  listGroupMembers,
  listGroupRepositoryGrants,
  putGroupCollectionGrant,
  putGroupRepositoryGrant,
  removeGroupMember,
  updateAdminGroup,
} from "@/api/groups";
import type {
  AddMembersRequest,
  AddMembersResponse,
  AdminGroup,
  AdminGroupMember,
  CollectionGrant,
  CreateGroupRequest,
  PutCollectionGrantRequest,
  PutRepositoryGrantRequest,
  RepositoryGrant,
  UUID,
  UpdateGroupRequest,
} from "@/api/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

/**
 * React Query hooks for the admin Groups + ACL API.
 *
 * Cache keys are nested by group id so adding/removing members or grants
 * invalidates only the affected group's panels, not the full list. The
 * top-level `["admin","groups"]` list does need an invalidation on
 * member/grant changes too, because the counts on each card depend on
 * those mutations.
 */

const GROUPS_KEY = ["admin", "groups"] as const;
const groupMembersKey = (id: UUID) => [...GROUPS_KEY, id, "members"] as const;
const groupReposKey = (id: UUID) => [...GROUPS_KEY, id, "repositories"] as const;
const groupCollectionsKey = (id: UUID) => [...GROUPS_KEY, id, "collections"] as const;

export function useAdminGroups() {
  return useQuery<AdminGroup[]>({
    queryKey: GROUPS_KEY,
    queryFn: listAdminGroups,
  });
}

export function useCreateAdminGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateGroupRequest) => createAdminGroup(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: GROUPS_KEY });
    },
  });
}

export function useUpdateAdminGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ groupId, payload }: { groupId: UUID; payload: UpdateGroupRequest }) =>
      updateAdminGroup(groupId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: GROUPS_KEY });
    },
  });
}

export function useDeleteAdminGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (groupId: UUID) => deleteAdminGroup(groupId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: GROUPS_KEY });
    },
  });
}

// --- members -------------------------------------------------------------

export function useGroupMembers(groupId: UUID | null) {
  return useQuery<AdminGroupMember[]>({
    queryKey: groupId ? groupMembersKey(groupId) : ["admin", "groups", "members", "noop"],
    queryFn: () => listGroupMembers(groupId as UUID),
    enabled: Boolean(groupId),
  });
}

export function useAddGroupMembers(groupId: UUID) {
  const qc = useQueryClient();
  return useMutation<AddMembersResponse, Error, AddMembersRequest>({
    mutationFn: (payload) => addGroupMembers(groupId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupMembersKey(groupId) });
      qc.invalidateQueries({ queryKey: GROUPS_KEY });
    },
  });
}

export function useRemoveGroupMember(groupId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: UUID) => removeGroupMember(groupId, userId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupMembersKey(groupId) });
      qc.invalidateQueries({ queryKey: GROUPS_KEY });
    },
  });
}

// --- repository grants ---------------------------------------------------

export function useGroupRepositoryGrants(groupId: UUID | null) {
  return useQuery<RepositoryGrant[]>({
    queryKey: groupId ? groupReposKey(groupId) : ["admin", "groups", "repos", "noop"],
    queryFn: () => listGroupRepositoryGrants(groupId as UUID),
    enabled: Boolean(groupId),
  });
}

export function usePutGroupRepositoryGrant(groupId: UUID) {
  const qc = useQueryClient();
  return useMutation<RepositoryGrant, Error, PutRepositoryGrantRequest>({
    mutationFn: (payload) => putGroupRepositoryGrant(groupId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupReposKey(groupId) });
      qc.invalidateQueries({ queryKey: GROUPS_KEY });
    },
  });
}

export function useDeleteGroupRepositoryGrant(groupId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repositoryId: UUID) => deleteGroupRepositoryGrant(groupId, repositoryId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupReposKey(groupId) });
      qc.invalidateQueries({ queryKey: GROUPS_KEY });
    },
  });
}

// --- collection grants ---------------------------------------------------

export function useGroupCollectionGrants(groupId: UUID | null) {
  return useQuery<CollectionGrant[]>({
    queryKey: groupId ? groupCollectionsKey(groupId) : ["admin", "groups", "collections", "noop"],
    queryFn: () => listGroupCollectionGrants(groupId as UUID),
    enabled: Boolean(groupId),
  });
}

export function usePutGroupCollectionGrant(groupId: UUID) {
  const qc = useQueryClient();
  return useMutation<CollectionGrant, Error, PutCollectionGrantRequest>({
    mutationFn: (payload) => putGroupCollectionGrant(groupId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupCollectionsKey(groupId) });
      qc.invalidateQueries({ queryKey: GROUPS_KEY });
    },
  });
}

export function useDeleteGroupCollectionGrant(groupId: UUID) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (collectionId: UUID) => deleteGroupCollectionGrant(groupId, collectionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: groupCollectionsKey(groupId) });
      qc.invalidateQueries({ queryKey: GROUPS_KEY });
    },
  });
}
