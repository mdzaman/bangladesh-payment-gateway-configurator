import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../api/endpoints";
import AuthLayout from "../../layout/AuthLayout";
import { useSession } from "../../lib/session";
import { ErrorNote, FormError, Loading } from "../../ui";

export default function Invite() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const { me, refresh, signOut } = useSession();
  const navigate = useNavigate();
  const preview = useQuery({ queryKey: ["invitation", token], queryFn: () => api.auth.invitation(token), enabled: Boolean(token), retry: false });
  const accept = useMutation({
    mutationFn: () => api.auth.acceptInvitation(token),
    onSuccess: async (ws) => { await refresh(); navigate(`/app/${ws.slug}/sandbox/overview`, { replace: true }); },
  });
  const here = `/invite?token=${encodeURIComponent(token)}`;
  return (
    <AuthLayout title="Join a workspace">
      {!token ? <p className="info bad">This link is incomplete. Open the link from the invitation email again.</p>
        : preview.isPending ? <Loading />
          : preview.error ? <ErrorNote error={preview.error} />
            : (
              <>
                <p>
                  {preview.data.inviter ? <><b>{preview.data.inviter}</b> invited</> : "You were invited as"} <b>{preview.data.email}</b> to
                  join <b>{preview.data.workspace}</b> as {preview.data.role}.
                </p>
                {me && me.user.email === preview.data.email && (
                  <>
                    <FormError error={accept.error} />
                    <button type="button" className="primary wide" disabled={accept.isPending} onClick={() => accept.mutate()}>
                      {accept.isPending ? "Joining…" : `Join ${preview.data.workspace}`}
                    </button>
                  </>
                )}
                {me && me.user.email !== preview.data.email && (
                  <>
                    <p className="info warn">You are signed in as {me.user.email}. This invitation is for a different address.</p>
                    <button type="button" className="wide" style={{ marginTop: 12 }} onClick={async () => { await signOut(); }}>Sign out</button>
                  </>
                )}
                {!me && preview.data.has_account && (
                  <Link className="btn primary wide" to={`/login?email=${encodeURIComponent(preview.data.email)}&next=${encodeURIComponent(here)}`}>Sign in to accept</Link>
                )}
                {!me && !preview.data.has_account && (
                  <Link className="btn primary wide" to={`/signup?invite=${encodeURIComponent(token)}&email=${encodeURIComponent(preview.data.email)}`}>Create an account to accept</Link>
                )}
              </>
            )}
    </AuthLayout>
  );
}
