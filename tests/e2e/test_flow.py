from playwright.sync_api import Page, expect

from conftest import SHOTS, shot


def test_home_links_to_the_main_areas(logged: Page) -> None:
    logged.goto("/")
    expect(logged.get_by_role("heading", name="Hola, Ana Demo")).to_be_visible()
    shot(logged, "00-inicio")


def test_week_grid_saves_hours_without_reloading(logged: Page) -> None:
    logged.goto("/horas")
    logged.fill("#buscar", "PJ-00001")
    logged.fill("#buscar", "PJ-0000")  # a second search must still find its list
    logged.locator("#resultados button").first.click()
    cell = logged.locator("input[name=horas]").first
    cell.fill("2,5")
    cell.press("Tab")
    logged.wait_for_load_state("networkidle")
    expect(logged.locator("input[name=horas]").first).to_have_value("2.50")
    shot(logged, "01-horas")


def test_project_list_and_a_returned_round(logged: Page) -> None:
    logged.goto("/proyectos")
    expect(logged.get_by_text("PJ-00001")).to_be_visible()
    shot(logged, "02-proyectos")
    logged.get_by_role("link", name="PJ-00001").first.click()
    expect(logged.get_by_text("Falta cubrir el caso de fechas límite.")).to_be_visible()
    shot(logged, "03-proyecto-rondas")


def test_creating_a_project(logged: Page) -> None:
    logged.goto("/proyectos/nuevo")
    logged.fill("#codigo", "PJ-90001")
    logged.fill("#titulo", "Proyecto de prueba E2E")
    logged.get_by_role("button", name="Guardar").click()
    expect(logged.get_by_text("Proyecto de prueba E2E").first).to_be_visible()


def test_gantt_renders_the_plan(logged: Page) -> None:
    logged.goto("/planificacion/gantt/proyectos")
    expect(logged.locator("svg").first).to_be_visible()
    shot(logged, "04-gantt")


def test_plan_vs_actual_and_closed_project(logged: Page) -> None:
    logged.goto("/informes/")
    shot(logged, "05-informes")
    logged.goto("/informes/proyectos")
    shot(logged, "06-plan-vs-real")
    logged.get_by_role("link", name="PJ-00003").first.click()
    expect(logged.get_by_text("Cerrado el")).to_be_visible()
    expect(logged.get_by_text("Las horas superaron lo planificado")).to_be_visible()
    shot(logged, "07-proyecto-cerrado")


def test_tables_page_search_and_speak_english(logged: Page) -> None:
    for n in range(1, 12):
        logged.goto("/proyectos/nuevo")
        logged.fill("#codigo", f"PJ-91{n:03d}")
        logged.fill("#titulo", f"Tabla larga {n}")
        logged.get_by_role("button", name="Guardar").click()
    logged.goto("/proyectos")
    table = logged.get_by_role("table", name="Proyectos")
    pager = logged.get_by_role("navigation", name="Paginación de la tabla")
    expect(table.locator("tbody tr:visible")).to_have_count(10)
    expect(pager.get_by_role("button", name="Primera página")).to_be_disabled()
    expect(pager.get_by_role("button", name="Página anterior")).to_be_disabled()
    pager.get_by_role("button", name="Página siguiente").click()
    expect(pager.get_by_role("button", name="Página 2")).to_have_attribute("aria-current", "page")
    expect(pager.get_by_role("button", name="Última página")).to_be_disabled()
    logged.get_by_label("Buscar:").fill("tabla larga 5")
    expect(table.locator("tbody tr:visible")).to_have_count(1)
    expect(logged.get_by_text("Mostrando 1 a 1 de 1 registros")).to_be_visible()
    logged.get_by_label("Buscar:").fill("")
    logged.get_by_label("Mostrar").select_option("20")
    expect(pager.get_by_role("button", name="Página siguiente")).to_be_disabled()
    shot(logged, "08-tabla-paginada")
    logged.get_by_role("button", name="English").click()
    expect(logged.get_by_role("heading", name="Projects")).to_be_visible()
    expect(logged.get_by_role("navigation", name="Table pagination")).to_be_visible()
    expect(logged.get_by_text("Showing 1 to 10 of")).to_be_visible()
    logged.goto("/informes/proyectos")
    expect(logged.get_by_text("Plan vs actual").first).to_be_visible()
    logged.get_by_role("button", name="Español").click()


def test_side_menu_widens_the_content_on_desktop(logged: Page) -> None:
    logged.goto("/admin/usuarios")
    sidebar = logged.locator("#hp-sidebar")
    toggle = logged.locator("#hp-nav-toggle")
    main = logged.locator("#contenido")
    expect(sidebar).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(logged.locator('#hp-sidebar a[aria-current="page"]')).to_have_text("Usuarios")
    width = main.bounding_box()["width"]
    shot(logged, "09-menu-lateral")
    toggle.click()
    expect(sidebar).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    logged.wait_for_timeout(300)  # padding transition
    assert main.bounding_box()["width"] > width
    logged.reload()  # the choice is remembered
    expect(sidebar).to_be_hidden()
    logged.locator("#hp-nav-toggle").click()
    expect(sidebar).to_be_visible()


def test_side_menu_overlays_the_content_on_mobile(logged: Page) -> None:
    logged.set_viewport_size({"width": 390, "height": 800})
    logged.goto("/proyectos")
    sidebar = logged.locator("#hp-sidebar")
    backdrop = logged.locator("#hp-backdrop")
    main = logged.locator("#contenido")
    expect(sidebar).to_be_hidden()
    width = main.bounding_box()["width"]
    logged.locator("#hp-nav-toggle").click()
    expect(sidebar).to_be_visible()
    expect(backdrop).to_have_css("opacity", "1")
    assert main.bounding_box()["width"] == width  # the menu covers, it does not push
    logged.wait_for_timeout(300)
    logged.screenshot(path=str(SHOTS / "10-menu-movil.png"))
    backdrop.click(position={"x": 370, "y": 400})
    expect(sidebar).to_be_hidden()
    logged.locator("#hp-nav-toggle").click()
    logged.keyboard.press("Escape")
    expect(sidebar).to_be_hidden()
