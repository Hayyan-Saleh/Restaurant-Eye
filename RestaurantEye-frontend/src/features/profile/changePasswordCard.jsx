import { useState } from "react";
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
  FieldDescription,
} from "@/components/ui/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { KeyRound } from "lucide-react";
import { requestOtp, resetPassword } from "@/features/auth/authApi";

function ChangePasswordCard() {
  const [step, setStep] = useState(1); // 1: request OTP, 2: enter OTP + new password
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  const handleRequestOtp = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await requestOtp(email);
      setSuccess("OTP sent to your email");
      setStep(2);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to send OTP");
    } finally {
      setLoading(false);
    }
  };

  const handleResetPassword = async (e) => {
    e.preventDefault();
    setError("");
    setSuccess("");

    if (newPassword !== confirmPassword) {
      setError("New passwords do not match");
      return;
    }
    if (newPassword.length < 8) {
      setError("New password must be at least 8 characters");
      return;
    }

    setLoading(true);
    try {
      await resetPassword(email, otp, newPassword);
      setSuccess("Password changed successfully");
      setStep(1);
      setEmail("");
      setOtp("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to reset password");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="max-w-md w-full">
      <form onSubmit={(e) => e.preventDefault()}>
        <CardHeader>
          <KeyRound className="size-8" />
          <CardTitle>Change Password</CardTitle>
          <CardDescription>Update your account password.</CardDescription>
        </CardHeader>
        <CardContent>
          <FieldGroup>
            {step === 1 ? (
              <>
                <Field data-invalid={!!error}>
                  <FieldLabel htmlFor="email">Email</FieldLabel>
                  <Input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    aria-invalid={!!error}
                  />
                  <FieldError>{error}</FieldError>
                </Field>
                {success && <p className="text-sm text-green-500">{success}</p>}
                <Button
                  type="button"
                  className="w-full"
                  disabled={loading}
                  onClick={handleRequestOtp}
                >
                  {loading ? "Sending..." : "Send OTP"}
                </Button>
              </>
            ) : (
              <>
                <Field>
                  <FieldLabel htmlFor="otp">OTP Code</FieldLabel>
                  <Input
                    id="otp"
                    type="text"
                    value={otp}
                    onChange={(e) => setOtp(e.target.value)}
                  />
                  <FieldDescription>Valid for 10 minutes.</FieldDescription>
                </Field>

                <Field data-invalid={!!error}>
                  <FieldLabel htmlFor="newPassword">New Password</FieldLabel>
                  <Input
                    id="newPassword"
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    aria-invalid={!!error}
                  />
                  <FieldDescription>At least 8 characters.</FieldDescription>
                </Field>

                <Field data-invalid={!!error}>
                  <FieldLabel htmlFor="confirmPassword">
                    Confirm New Password
                  </FieldLabel>
                  <Input
                    id="confirmPassword"
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    aria-invalid={!!error}
                  />
                  <FieldError>{error}</FieldError>
                </Field>

                {success && <p className="text-sm text-green-500">{success}</p>}

                <Button
                  type="button"
                  className="w-full"
                  disabled={loading}
                  onClick={handleResetPassword}
                >
                  {loading ? "Updating..." : "Update Password"}
                </Button>
              </>
            )}
          </FieldGroup>
        </CardContent>
      </form>
    </Card>
  );
}

export default ChangePasswordCard;
