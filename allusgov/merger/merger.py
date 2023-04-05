from rapidfuzz import process, utils
from bigtree import (
    levelorder_iter,
)
import polars as pl

from ..utils.utils import full_name


class Merger:
    """
    Merge class for merging two trees based on a similarity metric.

    Attributes:
        logger (logging.Logger): Logger object for logging messages.
        base_tree (Node): Base tree to be merged with.
        base_name (str): Source name for the base tree.
        source_tree (Node): Source tree to be merged.
        source_name (str): Source name for the source tree.
    """

    def __init__(self, logger, base_tree, base_name, source_tree, source_name):
        self.logger = logger
        self.base_tree = base_tree
        self.base_name = base_name
        self.source_tree = source_tree
        self.source_name = source_name
        self.source_names = self.name_list(self.source_tree, self.source_name)
        self.base_names = self.name_list(self.base_tree, self.base_name)
        self.similarity = self.calculate_similarity()

    def name_list(self, tree, source_name):
        """
        Generate a dictionary of names and their corresponding nodes in a tree.

        Args:
            tree (Node): Tree to extract names from.
            source_name (str): Source name for the tree.

        Returns:
            dict: Dictionary of names and their corresponding nodes.
        """
        names = {}
        for org in levelorder_iter(tree):
            name = full_name(org, source_name)
            if name not in names:
                names[name] = []
            names[name].append(org)
        return names

    def calculate_similarity(self):
        """
        Calculate string similarity for the source tree against the base tree.

        Returns:
            pl.DataFrame: Similarity dataframe.
        """
        self.logger.info(
            f"Calculating string similarity for {self.source_name} against the base tree..."
        )
        matrix = process.cdist(
            self.source_names, self.base_names, processor=utils.default_process
        )
        similarity = pl.DataFrame(matrix, schema=self.base_names.keys()).transpose(
            include_header=True,
            header_name="base",
            column_names=self.source_names.keys(),
        )
        return similarity

    def get_candidates(self, source_org_name):
        """
        Get candidates for a given source organization name.

        Args:
            source_org_name (str): Source organization name.
            similarity (polars.DataFrame): Similarity DataFrame.

        Returns:
            dict: Dictionary of candidate base organizations and their scores.
        """
        candidates = {}
        matches = (
            self.similarity.select(["base", source_org_name])
            .sort([source_org_name, "base"], descending=True)
            .head(5)
        )
        for match in matches.rows():  # pylint: disable=not-an-iterable
            base = match[0]
            score = match[1]
            base_orgs = self.base_names[base]
            for base_org in base_orgs:
                candidates[base_org] = score
        return candidates

    def update_parent_scores(
        self, candidates, current_source_org, current_base_org, factor
    ):
        """
        Update parent scores for the given candidates.

        Args:
            candidates (dict): Dictionary of candidate base organizations and their scores.
            current_source_org (Node): Current source organization node.
            current_base_org (Node): Current base organization node.
            factor (float): Factor for weighting parent scores.

        Returns:
            dict:
            dict: Updated candidates dictionary with parent scores.
        """
        current_source_org_name = full_name(current_source_org, self.source_name)
        current_base_org_name = full_name(current_base_org, self.base_name)
        parent_score = (  # pylint: disable-next=unsubscriptable-object
            self.similarity.select(["base", current_source_org_name])
            .filter(pl.col("base") == current_base_org_name)
            .head(1)
            .rows()[0][1]
        )

        for base_org in candidates.keys():
            candidates[base_org] = (candidates[base_org] + (parent_score * factor)) / (
                1 + factor
            )
            self.logger.debug(
                f"{candidates[base_org]:.1f}: adding score {parent_score:.1f} at factor {factor:.1f} for parents {current_source_org_name} & {current_base_org_name}"
            )
        return candidates

    def process_candidates(self, candidates, source_org):
        """
        Process candidates for a given source organization.

        Args:
            candidates (dict): Dictionary of candidate base organizations and their scores.
            source_org (Node): Source organization node.

        Returns:
            Node: Selected base organization to merge.
            float: Score of the selected base organization.
        """
        for base_org, score in candidates.items():
            if not source_org.is_root and not base_org.is_root:
                current_source_org = source_org.parent
                current_base_org = base_org.parent
                factor = 0.5
                self.logger.debug(
                    f"{candidates[base_org]:.1f}: candidate: {current_source_org.node_name} & {current_base_org.node_name}"
                )

                candidates = self.update_parent_scores(
                    candidates, current_source_org, current_base_org, factor
                )

        selection = sorted(candidates.items(), key=lambda x: x[1], reverse=True)[0][0]
        score = candidates[selection]

        return selection, score

    def merge(self):
        """
        Merge the source tree into the base tree based on string similarity.

        Returns:
            Node: Merged base tree.
        """
        self.logger.info(
            f"Checking for {self.source_name} matches against the base tree..."
        )
        source_orgs = [source_org for source_org in levelorder_iter(self.source_tree)]
        source_orgs.reverse()

        for source_org in source_orgs:
            source_org_name = full_name(source_org, self.source_name)
            candidates = self.get_candidates(source_org_name)

            self.logger.debug(f"Checking {len(candidates)} for {source_org_name}")

            selection, score = self.process_candidates(candidates, source_org)

            if score > 95:
                self.logger.info(
                    f"{score:.1f}: Selected candidate {selection.path_name} for {source_org.path_name}"
                )
                # Merge attributes to base tree.
                selection.set_attrs(
                    {self.source_name: source_org.get_attr(self.source_name)}
                )
                # Merge children
                for child in source_org.children:
                    self.logger.debug(
                        f"Merging child {child.path_name} into {selection.path_name}"
                    )
                    child.parent = selection
            else:
                self.logger.debug(
                    f"{score:.1f}: Skipped candidate {selection.path_name} for {source_org.path_name}"
                )

        return self.base_tree
