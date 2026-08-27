import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Card,
  CardTitle,
  CardContent,
  CardHeader,
  CardDescription,
} from "@/components/ui/card";
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldError,
} from "@/components/ui/field";
import { Button } from "@/components/ui/button";
import { UserLock } from "lucide-react";
import { Input } from "@/components/ui/input";
import { ModeToggle } from "@/components/web/mode-toggle";
import { login } from "./authApi";

function Auth() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await login(email, password);
      localStorage.setItem("token", res.data.access_token);
      navigate("/dashboard");
    } catch (err) {
      const detail = err.response?.data?.detail;
      const message = Array.isArray(detail)
        ? detail.map((d) => d.msg).join(", ")
        : detail || "Invalid email or password";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-background w-full">
      <Card className="max-w-md w-full">
        <form onSubmit={handleSubmit}>
          <CardHeader>
            <UserLock className="mx-auto size-10" />
            <CardTitle className="text-2xl font-bold text-center mb-4">
              Save Portal
            </CardTitle>
            <CardDescription className="text-center mb-4">
              Please enter your password to access the application.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <FieldGroup>
              <Field data-invalid={!!error}>
                <FieldLabel htmlFor="email">Email</FieldLabel>
                <Input
                  id="email"
                  type="email"
                  placeholder="Enter your email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  aria-invalid={!!error}
                />
              </Field>
              <Field data-invalid={!!error}>
                <FieldLabel htmlFor="password">Password</FieldLabel>
                <Input
                  id="password"
                  type="password"
                  placeholder="Enter your password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={!!error}
                />
                <FieldError>{error}</FieldError>
              </Field>
              <Button type="submit" className="w-full" disabled={loading}>
                {loading ? "Logging in..." : "Login"}
              </Button>
            </FieldGroup>
          </CardContent>
        </form>
      </Card>
      <div className="absolute bottom-4 right-4">
        <ModeToggle />
      </div>
    </div>
  );
}

export default Auth;
