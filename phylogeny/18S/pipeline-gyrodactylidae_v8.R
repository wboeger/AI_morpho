# =================================================================================
# R Pipeline for Phylogenetic Analysis with Gyrodactylidae 18S (v8 - REVERTED)
# =================================================================================

# --- Load required libraries ---
if (!requireNamespace("rentrez", quietly = TRUE)) install.packages("rentrez")
if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
if (!requireNamespace("Biostrings", quietly = TRUE)) BiocManager::install("Biostrings")
if (!requireNamespace("ape", quietly = TRUE)) install.packages("ape")
if (!requireNamespace("httr", quietly = TRUE)) install.packages("httr")
if (!requireNamespace("xml2", quietly = TRUE)) install.packages("xml2")
if (!requireNamespace("dplyr", quietly = TRUE)) install.packages("dplyr")

library(rentrez)
library(Biostrings)
library(ape)
library(httr)
library(xml2)
library(dplyr)


# --- ETAPA 1: DEFINIR VARIÁVEIS ---
message(">>> ETAPA 1: Definindo variáveis...")

target_taxon <- "Gyrodactylidae"
gene_query <- '(small subunit ribosomal RNA[All Fields] OR 18S[All Fields]) NOT (internal transcribed spacer[All Fields])'

outgroup_definitions <- list(
  Oogyrodactylidae = list(mode = "each_genus", n = 2)
)

known_outgroup_genera <- c("Aglaiogyrodactylus", "Onychogyrodactylus", "Phanerothecium", 
                           "Hyperopletes", "Oogyrodactylus")

fasta_out <- "Gyrodactylidae18S_v8.fa"
aligned_fasta <- "Gyrodactylidae18S_aln_v8.fa"
trimmed_fasta <- "Gyrodactylidae18Strimmed_v8.fa"
trimal_report <- "trimal_report18S_v8.html"
cipres_results_dir <- "cipres_results_18Sgyrodactylidae_v8"

# --- ETAPA 2: BUSCA E DOWNLOAD DAS SEQUÊNCIAS ---
message(">>> ETAPA 2: Buscando e baixando sequências...")

fetch_and_process_sequences <- function(query, remove_duplicates = TRUE, min_length = 400) {
  search_res <- entrez_search(db = "nuccore", term = query, retmax = 10000)
  message(paste(">>> Encontrados", search_res$count, "registros para a consulta."))
  if (search_res$count == 0) return(NULL)
  
  all_fasta_recs <- character()
  batch_size <- 200
  id_batches <- split(search_res$ids, ceiling(seq_along(search_res$ids) / batch_size))
  
  for (i in seq_along(id_batches)) {
    ids <- id_batches[[i]]
    message(paste(">>> Baixando lote de IDs:", i, "de", length(id_batches)))
    recs <- NULL
    try(recs <- entrez_fetch(db = "nuccore", id = ids, rettype = "fasta"), silent = TRUE)
    if(is.null(recs)) { Sys.sleep(2); try(recs <- entrez_fetch(db = "nuccore", id = ids, rettype = "fasta"), silent = TRUE) }
    if(is.null(recs)) stop("Falha ao baixar lote de IDs.")
    all_fasta_recs <- c(all_fasta_recs, recs)
  }
  fasta_recs <- paste(all_fasta_recs, collapse = "\n")
  
  if (is.null(fasta_recs) || nchar(fasta_recs) == 0) return(NULL)
  
  temp_fasta_file <- tempfile(fileext = ".fa")
  writeLines(fasta_recs, con = temp_fasta_file)
  seqs <- readDNAStringSet(temp_fasta_file)
  unlink(temp_fasta_file)
  
  if (remove_duplicates) {
    seqs <- seqs[!duplicated(as.character(seqs))]
    message(paste(">>> Sequências duplicadas removidas. Restam:", length(seqs)))
  }
  
  seqs_filtered <- seqs[width(seqs) >= min_length]
  message(paste(">>> Sequências <", min_length, "bp removidas. Restam:", length(seqs_filtered)))
  
  names(seqs_filtered) <- sapply(names(seqs_filtered), function(name) {
    parts <- strsplit(name, " ")[[1]]
    accession <- parts[1]
    species <- paste(parts[2:min(3, length(parts))], collapse = "_")
    paste0(accession, "|", species)
  })
  
  return(seqs_filtered)
}

# 2.1: Ingroup
gyros_query <- paste0('"', target_taxon, '"[Organism] AND ', gene_query)
message(paste(">>> Query Target:", gyros_query))
gyros18S_seqs_raw <- fetch_and_process_sequences(gyros_query, min_length = 400)

if (is.null(gyros18S_seqs_raw) || length(gyros18S_seqs_raw) == 0) {
  stop("Nenhuma sequência encontrada para o táxon alvo.")
}

# --- REMOVER SEQUÊNCIA ESPECÍFICA SOLICITADA ---
bad_accessions <- c("EU186159.1", "AY490406.1")
message(paste(">>> Verificando presença de sequências problemáticas para remoção:", paste(bad_accessions, collapse=", ")))
names_found <- names(gyros18S_seqs_raw)
# Cria um padrão regex para qualquer um dos bad_accessions
bad_pattern <- paste(bad_accessions, collapse = "|")
is_bad <- grepl(bad_pattern, names_found)

if (any(is_bad)) {
  message(paste(">>> Removendo", length(which(is_bad)), "sequências problemáticas encontradas:"))
  message(paste(names_found[is_bad], collapse="\n"))
  gyros18S_seqs_raw <- gyros18S_seqs_raw[!is_bad]
}

message(paste(">>> Filtrando sequências de", target_taxon, "para manter apenas a mais longa por espécie..."))
df_target <- data.frame(
    name = names(gyros18S_seqs_raw),
    species = sapply(strsplit(names(gyros18S_seqs_raw), "|", fixed = TRUE), `[`, 2),
    width = width(gyros18S_seqs_raw),
    stringsAsFactors = FALSE
)
df_target_unique <- df_target %>%
    group_by(species) %>%
    arrange(desc(width)) %>%
    slice_head(n = 1) %>%
    ungroup()
gyros18S_seqs <- gyros18S_seqs_raw[names(gyros18S_seqs_raw) %in% df_target_unique$name]
message(paste(">>> Após filtro de espécie única, restam", length(gyros18S_seqs), "sequências para", target_taxon))

# 2.2: Outgroups
final_outgroups <- DNAStringSet()
selected_species_names <- df_target_unique$species

for (family_name in names(outgroup_definitions)) {
  definition <- outgroup_definitions[[family_name]]
  message(paste("\n>>> Processando outgroup da família:", family_name))
  family_query <- paste0('"', family_name, '"[Organism] AND ', gene_query)
  family_seqs <- fetch_and_process_sequences(family_query, min_length = 400)
  
  if (is.null(family_seqs) || length(family_seqs) == 0) next
  
  current_species_all <- sapply(strsplit(names(family_seqs), "|", fixed = TRUE), `[`, 2)
  is_target <- grepl(target_taxon, names(family_seqs), ignore.case = TRUE)
  is_already_selected <- current_species_all %in% selected_species_names
  candidate_seqs_unfiltered <- family_seqs[!is_target & !is_already_selected]

  if (length(candidate_seqs_unfiltered) == 0) next

  df_unfiltered <- data.frame(
      name = names(candidate_seqs_unfiltered),
      species = sapply(strsplit(names(candidate_seqs_unfiltered), "|", fixed = TRUE), `[`, 2),
      width = width(candidate_seqs_unfiltered),
      stringsAsFactors = FALSE
  )
  df_unique_species <- df_unfiltered %>%
      group_by(species) %>%
      arrange(desc(width)) %>%
      slice_head(n = 1) %>%
      ungroup()
  candidate_seqs <- candidate_seqs_unfiltered[names(candidate_seqs_unfiltered) %in% df_unique_species$name]
  
  candidate_df <- data.frame(
      name = names(candidate_seqs),
      species = sapply(strsplit(names(candidate_seqs), "|", fixed = TRUE), `[`, 2),
      genus = sapply(strsplit(sapply(strsplit(names(candidate_seqs), "|", fixed = TRUE), `[`, 2), "_"), `[`, 1),
      width = width(candidate_seqs),
      stringsAsFactors = FALSE
  )
  
  newly_selected_seqs <- DNAStringSet()
  if (definition$mode == "each_genus") {
    selected_df <- candidate_df %>%
        group_by(genus) %>%
        arrange(desc(width)) %>%
        slice_head(n = definition$n) %>%
        ungroup()
    newly_selected_seqs <- candidate_seqs[names(candidate_seqs) %in% selected_df$name]
  } else if (definition$mode == "top_species") {
    selected_df <- candidate_df %>%
        arrange(desc(width)) %>%
        slice_head(n = definition$n)
    newly_selected_seqs <- candidate_seqs[names(candidate_seqs) %in% selected_df$name]
  }
  
  if (length(newly_selected_seqs) > 0) {
    final_outgroups <- c(final_outgroups, newly_selected_seqs)
    new_species <- sapply(strsplit(names(newly_selected_seqs), "|", fixed = TRUE), `[`, 2)
    selected_species_names <- c(selected_species_names, new_species)
    message(paste(">>> Adicionados", length(newly_selected_seqs), "novos outgroups de", family_name))
  }
}

final_sequences <- c(gyros18S_seqs, final_outgroups)
final_sequences <- final_sequences[!duplicated(as.character(final_sequences))]

message(paste("\n>>> Total final de sequências únicas para análise:", length(final_sequences)))
writeXStringSet(final_sequences, filepath = fasta_out)


# --- ETAPA 3: ALINHAMENTO COM MAFFT ---
message(">>> ETAPA 3: Alinhando sequências com MAFFT (AdjustDirection)...")
mafft_path <- Sys.which("mafft")
system2(mafft_path, args = c("--auto", "--thread", "-1", "--adjustdirection", fasta_out), stdout = aligned_fasta)


# --- ETAPA 4: LIMPEZA DO ALINHAMENTO COM TRIMAl ---
message(">>> ETAPA 4: Limpando o alinhamento com trimAl...")
trimal_path <- Sys.which("trimal")
trimal_command <- paste(trimal_path, "-in", aligned_fasta, "-out", trimmed_fasta, "-gappyout", "-htmlout", trimal_report)
system(trimal_command)


# --- ETAPA 5: SUBMISSÃO NO CIPRES ---
message(">>> ETAPA 5: Submetendo a análise para o CIPRES...")

cra_user <- "wboeger"
password <- "TerE4dUwSacY2ut" 
app_key <- "RAXML_WB-89AD4AFD396E40258AE39C61A9BF9AE9"
cipres_url <- "https://cipresrest.sdsc.edu/cipresrest/v1"

# --- REVERTIDO PARA O COMANDO ORIGINAL SEM PARAMETRO DE MODELO EXTRA ---
curl_command <- paste0(
  "curl -u ", cra_user, ":", password,
  " -H cipres-appkey:", app_key,
  " ", cipres_url, "/job/", cra_user,
  " -F tool=RAXMLNG_XSEDE",
  " -F input.infile_=@", normalizePath(trimmed_fasta),
  " -F vparam.select_analysis_=all",
  " -F vparam.specify_bootstraps_=1000"
)

submission_output_file <- "cipres_submission_output.xml"
system(paste(curl_command, ">", submission_output_file))

if (file.exists(submission_output_file) && file.info(submission_output_file)$size > 0) {
  xml_response <- read_xml(submission_output_file)
  job_status_url_node <- xml_find_first(xml_response, ".//selfUri/url")
  if (!is.na(job_status_url_node)) {
    job_status_url <- xml_text(job_status_url_node)
    
    # --- ETAPA 6: AGUARDANDO ---
    job_finished <- FALSE
    while (!job_finished) {
      Sys.sleep(60) 
      status_response <- GET(job_status_url, config = c(authenticate(cra_user, password), add_headers("cipres-appkey" = app_key)))
      if (http_status(status_response)$category == "Success") {
        status_xml <- read_xml(content(status_response, "text", encoding = "UTF-8"))
        xml_ns_strip(status_xml)
        job_stage <- xml_text(xml_find_first(status_xml, ".//jobStage"))
        message(paste(">>> Status do job:", job_stage))
        if (job_stage %in% c("COMPLETED", "TERMINATED", "FAILED", "SUSPENDED")) job_finished <- TRUE
      }
    }
    
    if (job_stage == "COMPLETED") {
      if (!dir.exists(cipres_results_dir)) dir.create(cipres_results_dir)
      results_list_url <- xml_text(xml_find_first(status_xml, ".//resultsUri/url"))
      list_response <- GET(results_list_url, config = c(authenticate(cra_user, password), add_headers("cipres-appkey" = app_key)))
      list_xml <- read_xml(content(list_response, "text", encoding = "UTF-8"))
      xml_ns_strip(list_xml)
      file_nodes <- xml_find_all(list_xml, ".//jobfile")
      for (node in file_nodes) {
        file_name <- xml_text(xml_find_first(node, ".//filename"))
        download_url <- xml_text(xml_find_first(node, ".//downloadUri/url"))
        dest_path <- file.path(cipres_results_dir, basename(file_name))
        GET(download_url, config = c(authenticate(cra_user, password), add_headers("cipres-appkey" = app_key)), write_disk(dest_path, overwrite = TRUE))
      }
    }
  }
}

# --- ETAPA 7: ENRAIZAR ---
message("\n>>> ETAPA 7: Enraizando a árvore final...")
raxml_tree_file <- file.path(cipres_results_dir, "infile.txt.raxml.bestTree")
if (!file.exists(raxml_tree_file)) {
  tmp_ml_file <- file.path(cipres_results_dir, "infile.txt.raxml.mlTrees.TMP")
  if (file.exists(tmp_ml_file)) raxml_tree_file <- tmp_ml_file
}

if (file.exists(raxml_tree_file)) {
    unrooted_tree <- read.tree(raxml_tree_file)
    if (inherits(unrooted_tree, "multiPhylo")) unrooted_tree <- unrooted_tree[[length(unrooted_tree)]]
    
    all_tips <- unrooted_tree$tip.label
    outgroup_in_tree <- character()
    for (gen in known_outgroup_genera) outgroup_in_tree <- c(outgroup_in_tree, grep(gen, all_tips, value = TRUE, ignore.case = TRUE))
    
    if (length(outgroup_in_tree) > 0) {
      rooted_tree <- NULL
      try({ rooted_tree <- root(unrooted_tree, outgroup = outgroup_in_tree, resolve.root = TRUE) }, silent = TRUE)
      if (is.null(rooted_tree)) {
        single_outgroup <- outgroup_in_tree[1]
        try({ rooted_tree <- root(unrooted_tree, outgroup = single_outgroup, resolve.root = TRUE) })
      }
      if (!is.null(rooted_tree)) {
        write.tree(rooted_tree, file = "gyros18S_rooted_v8.tre")
        pdf("gyros18S_rooted_tree_v8.pdf", width = 10, height = 18)
        plot(rooted_tree, cex = 0.5)
        add.scale.bar()
        title("Árvore Filogenética Enraizada (v8)")
        dev.off()
      }
    }
}
message(">>> Fim do Pipeline.")
