"""What a route may ask of the page's member."""

from __future__ import annotations

from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient

from hoard import plugin_manifest
from hoard.api import router as api_router
from hoard.api.deps import AdminDep, SeesCostsDep
from seneschal_core.api import install


async def test_costs_show_to_a_member_holding_the_permission_and_admin_routes_to_admins(
    app: FastAPI, sign_in
):
    probe = FastAPI()
    install(probe, app.state.plugin)
    probe.include_router(api_router)

    @probe.get("/costs")
    async def sees_costs(sees: SeesCostsDep) -> bool:
        return sees

    @probe.get("/admin")
    async def administer(session: AdminDep) -> str:
        return session.member_ref

    async with AsyncClient(transport=ASGITransport(app=probe), base_url="http://test") as client:
        await sign_in(client)
        assert (await client.get("/costs")).json() is False
        refused = await client.get("/admin")
        assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

        await sign_in(client, (plugin_manifest.SEE_COSTS,), admin=True, initiative_id=None)
        assert (await client.get("/costs")).json() is True
        assert (await client.get("/admin")).status_code == 200
