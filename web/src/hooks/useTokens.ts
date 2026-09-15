import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type CreateTokenInput,
  createToken,
  listTokens,
  revokeToken,
  rotateToken,
  type TokenCreated,
  type TokenView,
} from "@/api/tokens";

const TOKENS_KEY = ["me", "tokens"] as const;

export function useTokens() {
  return useQuery<TokenView[]>({
    queryKey: TOKENS_KEY,
    queryFn: listTokens,
  });
}

export function useCreateToken() {
  const qc = useQueryClient();
  return useMutation<TokenCreated, Error, CreateTokenInput>({
    mutationFn: (input: CreateTokenInput) => createToken(input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: TOKENS_KEY });
    },
  });
}

export function useRevokeToken() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (tokenId: string) => revokeToken(tokenId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: TOKENS_KEY });
    },
  });
}

export function useRotateToken() {
  const qc = useQueryClient();
  return useMutation<TokenCreated, Error, string>({
    mutationFn: (tokenId: string) => rotateToken(tokenId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: TOKENS_KEY });
    },
  });
}
