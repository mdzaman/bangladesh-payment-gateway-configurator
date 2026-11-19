import { Link } from "react-router-dom";
import AuthLayout from "../../layout/AuthLayout";

export default function NotFound() {
  return (
    <AuthLayout title="Page not found">
      <p>There is nothing at this address.</p>
      <p style={{ marginBottom: 0 }}><Link className="btn primary" to="/">Go to the start</Link></p>
    </AuthLayout>
  );
}
