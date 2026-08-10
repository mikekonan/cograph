import { apiJson } from "@/api/client";
import type {
  AddMembersRequest,
  AddMembersResponse,
  AdminGroup,
  AdminGroupListResponse,
  AdminGroupMember,
  AdminGroupMembersResponse,
  CollectionGrant,
  CollectionGrantListResponse,
  CreateGroupRequest,
  PutCollectionGrantRequest,
  PutRepositoryGrantRequest,
  RepositoryGrant,
  RepositoryGrantListResponse,
  UUID,
  UpdateGroupRequest,
} from "@/api/types";

/**
 * Client for `/api/admin/groups`. Mirrors `backend/app/api/admin_groups.py`
 * 1:1. Member-add is idempotent (200 with split added / already_present);
 * grants are upserts (200 on both create and level-change). All mutating
 * calls go through `apiJson`, which attaches the CSRF cookie.
 */

export async function listAdminGroups(): Promise<AdminGroup[]> {
  const body = await apiJson<AdminGroupListResponse>("/api/admin/groups");
  return body.items;
}

export async function createAdminGroup(payload: CreateGroupRequest): Promise<AdminGroup> {
  return apiJson<AdminGroup>("/api/admin/groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateAdminGroup(
  groupId: UUID,
  payload: UpdateGroupRequest,
): Promise<AdminGroup> {
  return apiJson<AdminGroup>(`/api/admin/groups/${groupId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminGroup(groupId: UUID): Promise<void> {
  await apiJson<void>(`/api/admin/groups/${groupId}`, { method: "DELETE" });
}

export async function listGroupMembers(groupId: UUID): Promise<AdminGroupMember[]> {
  const body = await apiJson<AdminGroupMembersResponse>(`/api/admin/groups/${groupId}/members`);
  return body.items;
}

export async function addGroupMembers(
  groupId: UUID,
  payload: AddMembersRequest,
): Promise<AddMembersResponse> {
  return apiJson<AddMembersResponse>(`/api/admin/groups/${groupId}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function removeGroupMember(groupId: UUID, userId: UUID): Promise<void> {
  await apiJson<void>(`/api/admin/groups/${groupId}/members/${userId}`, {
    method: "DELETE",
  });
}

export async function listGroupRepositoryGrants(groupId: UUID): Promise<RepositoryGrant[]> {
  const body = await apiJson<RepositoryGrantListResponse>(
    `/api/admin/groups/${groupId}/repositories`,
  );
  return body.items;
}

export async function putGroupRepositoryGrant(
  groupId: UUID,
  payload: PutRepositoryGrantRequest,
): Promise<RepositoryGrant> {
  return apiJson<RepositoryGrant>(`/api/admin/groups/${groupId}/repositories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteGroupRepositoryGrant(groupId: UUID, repositoryId: UUID): Promise<void> {
  await apiJson<void>(`/api/admin/groups/${groupId}/repositories/${repositoryId}`, {
    method: "DELETE",
  });
}

export async function listGroupCollectionGrants(groupId: UUID): Promise<CollectionGrant[]> {
  const body = await apiJson<CollectionGrantListResponse>(
    `/api/admin/groups/${groupId}/collections`,
  );
  return body.items;
}

export async function putGroupCollectionGrant(
  groupId: UUID,
  payload: PutCollectionGrantRequest,
): Promise<CollectionGrant> {
  return apiJson<CollectionGrant>(`/api/admin/groups/${groupId}/collections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteGroupCollectionGrant(groupId: UUID, collectionId: UUID): Promise<void> {
  await apiJson<void>(`/api/admin/groups/${groupId}/collections/${collectionId}`, {
    method: "DELETE",
  });
}
