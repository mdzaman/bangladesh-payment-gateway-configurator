import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { forgetSession } from "./session";

describe("signing out", () => {
  it("tells the page that is on screen, and forgets what was loaded", async () => {
    const client = new QueryClient();
    client.setQueryData(["me"], { user: { email: "owner@example.com" } });
    client.setQueryData(["platform"], { brand_name: "bdpay cloud" });
    client.setQueryData(["env", "shop", "live", "orders"], { data: ["INV1001"] });
    client.setQueryData(["two-factor"], { enabled: true });
    // What the mounted SessionProvider is: an observer of ["me"] that was there before sign-out.
    const observer = new QueryObserver(client, { queryKey: ["me"], enabled: false });
    const seen: unknown[] = [];
    const stop = observer.subscribe((result) => seen.push(result.data));

    forgetSession(client);

    expect(observer.getCurrentResult().data).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(seen.at(-1)).toBeNull();
    expect(client.getQueryData(["env", "shop", "live", "orders"])).toBeUndefined();
    expect(client.getQueryData(["two-factor"])).toBeUndefined();
    expect(client.getQueryData(["platform"])).toEqual({ brand_name: "bdpay cloud" }); // public, the same for everyone
    stop();
  });
});
