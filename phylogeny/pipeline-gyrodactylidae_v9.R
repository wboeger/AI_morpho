# =================================================================================
# R Pipeline for Phylogenetic Analysis with Gyrodactylidae 18S (v9)
# Based on v8, but starts from existing trimmed alignment and removes specific sequence.
# =================================================================================

# --- Load required libraries ---
if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
if (!requireNamespace("Biostrings", quietly = TRUE)) BiocManager::install("Biostrings")
if (!requireNamespace("ape", quietly = TRUE)) install.packages("ape")
if (!requireNamespace("httr", quietly = TRUE)) install.packages("httr")
if (!requireNamespace("xml2", quietly = TRUE)) install.packages("xml2")
if (!requireNamespace("dplyr", quietly = TRUE)) install.packages("dplyr")

library(Biostrings)
library(ape)
library(httr)
library(xml2)
library(dplyr)

# --- ETAPA 1: DEFINIR VARIÁVEIS ---
message(">>> ETAPA 1: Definindo variáveis (v9)...")

input_fasta_v8 <- "Gyrodactylidae18Strimmed_v8.fa"
trimmed_fasta_v9 <- "Gyrodactylidae18Strimmed_v9.fa"
cipres_results_dir <- "cipres_results_18Sgyrodactylidae_v9"

known_outgroup_genera <- c("Aglaiogyrodactylus", "Onychogyrodactylus", "Phanerothecium", 
                           "Tresuncinidactylus", "Mormyrogyrodactylus", "Diplogyrodactylus",
                           "Hyperopletes", "Oogyrodactylus")

# --- ETAPA 2: CARREGAR SEQUÊNCIAS E FILTRAR ---
message(">>> ETAPA 2: Carregando alinhamento v8 e filtrando sequências...")

if (!file.exists(input_fasta_v8)) {
  stop(paste("Arquivo de entrada não encontrado:", input_fasta_v8))
}

seqs <- readDNAStringSet(input_fasta_v8)
message(paste(">>> Total de sequências carregadas:", length(seqs)))

# Sequence to remove: AY490415.1|Gyrodactylus salaris
target_to_remove <- "AY490415.1"
message(paste(">>> Removendo sequência com accession:", target_to_remove))

# Filter
is_target <- grepl(target_to_remove, names(seqs), fixed = TRUE)
if (any(is_target)) {
  message(paste(">>> Sequência encontrada e removida:", names(seqs)[is_target]))
  seqs_filtered <- seqs[!is_target]
} else {
  message(">>> AVISO: Sequência alvo não encontrada no arquivo.")
  seqs_filtered <- seqs
}

message(paste(">>> Total de sequências após filtro:", length(seqs_filtered)))

# Save v9
writeXStringSet(seqs_filtered, filepath = trimmed_fasta_v9)
message(paste(">>> Novo arquivo alinhado e filtrado salvo em:", trimmed_fasta_v9))


# --- ETAPA 3 & 4: (PULADAS - JÁ ESTAMOS COM ALINHAMENTO TRIMADO) ---
message(">>> ETAPA 3 & 4: Pulando alinhamento e trimming (usando entrada pré-processada).")


# --- ETAPA 5: SUBMISSÃO NO CIPRES ---
message(">>> ETAPA 5: Submetendo a análise para o CIPRES (v9)...")

cra_user <- "wboeger"
password <- "TerE4dUwSacY2ut" 
app_key <- "RAXML_WB-89AD4AFD396E40258AE39C61A9BF9AE9"
cipres_url <- "https://cipresrest.sdsc.edu/cipresrest/v1"

curl_command <- paste0(
  "curl -u ", cra_user, ":", password,
  " -H cipres-appkey:", app_key,
  " ", cipres_url, "/job/", cra_user,
  " -F tool=RAXMLNG_XSEDE",
  " -F input.infile_=@", normalizePath(trimmed_fasta_v9),
  " -F vparam.select_analysis_=all",
  " -F vparam.specify_bootstraps_=1000"
)

submission_output_file <- "cipres_submission_output_v9.xml"
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
message("\n>>> ETAPA 7: Enraizando a árvore final (v9)...")
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
        write.tree(rooted_tree, file = "gyros18S_rooted_v9.tre")
        pdf("gyros18S_rooted_tree_v9.pdf", width = 10, height = 18)
        plot(rooted_tree, cex = 0.5)
        add.scale.bar()
        title("Árvore Filogenética Enraizada (v9)")
        dev.off()
        message(">>> Árvore v9 gerada com sucesso.")
      }
    } else {
        message(">>> AVISO: Nenhum outgroup conhecido encontrado na árvore para enraizamento.")
    }
} else {
    message(">>> AVISO: Arquivo de árvore RAxML não encontrado.")
}

message(">>> Fim do Pipeline v9.")

