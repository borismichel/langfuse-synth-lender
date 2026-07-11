import type { Session } from "next-auth";
import { SignJWT } from "jose";

const encoder = new TextEncoder();

function requiredSecret(): Uint8Array {
  const secret = process.env.PORTAL_API_JWT_SECRET || process.env.AUTH_SECRET;

  if (!secret) {
    throw new Error("PORTAL_API_JWT_SECRET or AUTH_SECRET is required");
  }

  return encoder.encode(secret);
}

export async function createApiSessionToken(session: Session): Promise<string> {
  const issuer = process.env.PORTAL_API_JWT_ISSUER || "lender-portal-web";
  const audience = process.env.PORTAL_API_JWT_AUDIENCE || "lender-portal-api";

  return new SignJWT({
    email: session.user.email,
    name: session.user.name,
    picture: session.user.image,
    hd: session.user.hd,
    email_verified: session.user.email_verified,
    scope: "use-cases:read",
  })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setSubject(session.user.id || session.user.email || "unknown")
    .setIssuer(issuer)
    .setAudience(audience)
    .setIssuedAt()
    .setExpirationTime("5m")
    .sign(requiredSecret());
}
