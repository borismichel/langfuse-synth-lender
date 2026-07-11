import { NextResponse } from "next/server";

import { auth } from "@/auth";

const publicPathPrefixes = ["/auth/signin", "/auth/error", "/api/auth"];

export default auth((request) => {
  const { pathname } = request.nextUrl;
  const isPublicPath = publicPathPrefixes.some((prefix) =>
    pathname.startsWith(prefix),
  );

  if (!request.auth && !isPublicPath) {
    const signInUrl = new URL("/auth/signin", request.url);
    signInUrl.searchParams.set("callbackUrl", request.nextUrl.href);
    return NextResponse.redirect(signInUrl);
  }

  if (request.auth && pathname === "/auth/signin") {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
});

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
