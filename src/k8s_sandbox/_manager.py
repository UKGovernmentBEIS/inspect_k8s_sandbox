from __future__ import annotations

import asyncio
from contextvars import ContextVar

from rich import box, print
from rich.panel import Panel
from rich.table import Table

from k8s_sandbox._helm import Release
from k8s_sandbox._helm import uninstall as helm_uninstall


class HelmReleaseManager:
    """
    Manages the lifecycle of Helm releases.

    Each instance of this class is scoped to a single async context.
    """

    _context_var: ContextVar[HelmReleaseManager] = ContextVar("k8s_manager_instance")

    def __init__(self) -> None:
        self._installed_releases: list[Release] = []

    @classmethod
    def get_instance(cls) -> HelmReleaseManager:
        """Gets the Manager instance for the current async context."""
        try:
            return cls._context_var.get()
        except LookupError:
            manager = cls()
            cls._context_var.set(manager)
            return manager

    async def install(self, release: Release) -> None:
        """
        Installs a release and tracks it for eventual cleanup.

        Args:
          release (Release): The release to install and track.
        """
        # Track the release regardless of the install result.
        self._installed_releases.append(release)
        await release.install()

    async def uninstall(self, release: Release, quiet: bool) -> None:
        """
        Uninstalls a release managed by this instance.

        Args:
          release (Release): The release to uninstall.
          quiet (bool): If True, suppress output to the console.
        """
        await release.uninstall(quiet)
        self._installed_releases.remove(release)

    async def uninstall_all(self, print_only: bool) -> None:
        """Uninstalls all releases managed by this instance.

        This method is not quiet i.e. it will print output to the console.

        Args:
          print_only (bool): If True, print cleanup instructions without actually
            uninstalling anything.
        """
        if len(self._installed_releases) == 0:
            return
        if print_only:
            self._print_cleanup_instructions()
            return
        _print_do_not_interrupt()
        tasks = [release.uninstall(quiet=False) for release in self._installed_releases]
        # Clear the list before awaiting the tasks to prevent other calls to this method
        # from interfering.
        self._installed_releases.clear()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _print_cleanup_instructions(self) -> None:
        table = Table(
            title="K8s Sandbox Environments (not yet cleaned up):",
            box=box.SQUARE_DOUBLE_HEAD,
            show_lines=True,
            title_style="bold",
            title_justify="left",
        )
        table.add_column("Container(s)", no_wrap=True)
        table.add_column("Cleanup")
        for release in self._installed_releases:
            table.add_row(
                release.release_name,
                f"[blue]inspect sandbox cleanup k8s {release.release_name}[/blue]",
            )
        print("")
        print(table)
        # TODO: Once supported, tell user how to cleanup all environments.
        # print(
        #     "\nCleanup all environments with: "
        #     "[blue]inspect sandbox cleanup k8s[/blue]\n"
        # )


async def uninstall_unmanaged_release(release_name: str) -> None:
    """
    Uninstall a Helm release which is not managed by a HelmReleaseManager.

    Args:
      release_name (str): The name of the release to uninstall (e.g. "lsphdyup").
    """
    _print_do_not_interrupt()
    await helm_uninstall(release_name, quiet=False)


def _print_do_not_interrupt() -> None:
    print(
        Panel(
            "[bold][blue]Cleaning up K8s resources (please do not interrupt this "
            "operation!):[/blue][/bold]",
        )
    )
