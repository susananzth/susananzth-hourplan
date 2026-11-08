from playwright.sync_api import Page, expect

from conftest import shot


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
