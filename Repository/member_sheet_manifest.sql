USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[member_sheet_manifest]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[member_sheet_manifest](
	[run_id] [uniqueidentifier] NOT NULL,
	[sheet_name] [nvarchar](50) NOT NULL,
	[s3_key] [nvarchar](1000) NOT NULL,
	[row_count] [int] NOT NULL,
	[col_count] [int] NOT NULL,
	[columns_json] [nvarchar](max) NOT NULL,
 CONSTRAINT [PK_member_sheet_manifest] PRIMARY KEY CLUSTERED 
(
	[run_id] ASC,
	[sheet_name] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO
ALTER TABLE [dbo].[member_sheet_manifest]  WITH CHECK ADD  CONSTRAINT [FK_member_sheet_manifest_run] FOREIGN KEY([run_id])
REFERENCES [dbo].[run] ([run_id])
GO
ALTER TABLE [dbo].[member_sheet_manifest] CHECK CONSTRAINT [FK_member_sheet_manifest_run]
GO
ALTER TABLE [dbo].[member_sheet_manifest]  WITH CHECK ADD  CONSTRAINT [CK_member_sheet_manifest_json] CHECK  ((isjson([columns_json])=(1)))
GO
ALTER TABLE [dbo].[member_sheet_manifest] CHECK CONSTRAINT [CK_member_sheet_manifest_json]
GO
