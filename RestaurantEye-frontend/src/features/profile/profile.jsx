import { useEffect, useState } from "react";
import ChangePasswordCard from "./changePasswordCard";
import { getMe } from "@/features/auth/authApi";

function Profile() {
  const [admin, setAdmin] = useState(null);

  useEffect(() => {
    getMe()
      .then((res) => setAdmin(res.data))
      .catch(() => setAdmin(null));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold">Admin Profile</h1>
      {admin && (
        <p className="text-sm text-muted-foreground mt-1">{admin.email}</p>
      )}
      <ChangePasswordCard />
    </div>
  );
}

export default Profile;
