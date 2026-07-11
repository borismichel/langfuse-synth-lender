import NextAuth, { type NextAuthConfig } from "next-auth";
import Google from "next-auth/providers/google";

type GoogleProfile = {
  email?: string;
  email_verified?: boolean | string;
  hd?: string;
  name?: string;
  picture?: string;
};

const workspaceDomain = process.env.GOOGLE_WORKSPACE_DOMAIN?.trim().toLowerCase();

function isWorkspaceProfile(profile?: GoogleProfile): boolean {
  if (!workspaceDomain || !profile?.email) {
    return false;
  }

  const email = profile.email.trim().toLowerCase();
  const emailDomain = email.split("@").at(-1);
  const hostedDomain = profile.hd?.trim().toLowerCase();
  const verified =
    profile.email_verified === true || profile.email_verified === "true";

  return (
    verified &&
    emailDomain === workspaceDomain &&
    hostedDomain === workspaceDomain
  );
}

const authorizationParams: Record<string, string> = {
  prompt: "select_account",
};

if (workspaceDomain) {
  authorizationParams.hd = workspaceDomain;
}

export const authConfig = {
  trustHost: true,
  session: {
    strategy: "jwt",
  },
  pages: {
    signIn: "/auth/signin",
    error: "/auth/error",
  },
  providers: [
    Google({
      clientId: process.env.AUTH_GOOGLE_ID,
      clientSecret: process.env.AUTH_GOOGLE_SECRET,
      authorization: {
        params: authorizationParams,
      },
    }),
  ],
  callbacks: {
    async signIn({ profile }) {
      return isWorkspaceProfile(profile as GoogleProfile | undefined);
    },
    async jwt({ token, profile }) {
      if (profile) {
        const googleProfile = profile as GoogleProfile;
        token.hd = googleProfile.hd;
        token.email_verified =
          googleProfile.email_verified === true ||
          googleProfile.email_verified === "true";
      }
      return token;
    },
    async session({ session, token }) {
      if (token.sub) {
        session.user.id = token.sub;
      }
      session.user.hd = typeof token.hd === "string" ? token.hd : undefined;
      session.user.email_verified = token.email_verified === true;
      return session;
    },
  },
} satisfies NextAuthConfig;

export const { handlers, auth, signIn, signOut } = NextAuth(authConfig);
