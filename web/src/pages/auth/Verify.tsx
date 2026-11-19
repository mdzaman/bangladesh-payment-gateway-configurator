import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import * as api from "../../api/endpoints";
import AuthLayout from "../../layout/AuthLayout";
import { useSession } from "../../lib/session";
import { ErrorNote, Loading } from "../../ui";

export default function Verify() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const { me } = useSession();
  const queryClient = useQueryClient();
  // A query, so the single-use link is spent exactly once however often this renders.
  const verify = useQuery({
    queryKey: ["verify-email", token], queryFn: () => api.auth.verifyEmail(token), enabled: Boolean(token),
    retry: false, staleTime: Infinity, gcTime: Infinity, refetchOnWindowFocus: false,
  });
  useEffect(() => {
    if (verify.isSuccess) queryClient.invalidateQueries({ queryKey: ["me"] });
  }, [verify.isSuccess, queryClient]);
  return (
    <AuthLayout title="Confirm your email">
      {!token ? <p className="info bad">This link is incomplete. Open the link from the email again.</p>
        : verify.isPending ? <Loading label="Confirming" />
          : verify.error ? <><ErrorNote error={verify.error} /><p className="small" style={{ marginTop: 12 }}>You can ask for a new link from your account page.</p></>
            : <p className="info"><b>{verify.data.email}</b> is confirmed.</p>}
      <p style={{ marginTop: 16, marginBottom: 0 }}><Link className="btn primary" to={me ? "/app" : "/login"}>{me ? "Go to the dashboard" : "Sign in"}</Link></p>
    </AuthLayout>
  );
}
